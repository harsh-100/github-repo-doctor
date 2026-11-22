"""Parse external tool output into Findings.

Rules: the tool's own message/rule is the evidence and is never altered; severity comes from a
fixed mapping; paths must resolve inside the repository; secrets are redacted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

from repo_doctor.analyzers.language import classify
from repo_doctor.core.models import Finding, Severity, Verification
from repo_doctor.security.permissions import PathSecurityError, safe_resolve
from repo_doctor.security.redaction import redact
from repo_doctor.tools.file_reader import RepoReader

MAX_FINDINGS_PER_TOOL = 200
TOOL_CONFIDENCE = 0.95


class ToolOutputError(Exception):
    """Tool output could not be parsed (reported, never silently ignored)."""


@dataclass
class ParseContext:
    root: Path
    reader: RepoReader
    ignore: pathspec.PathSpec
    excluded_dirs: set[str]
    omitted: int = field(default=0)  # findings dropped by the per-tool cap


def rel_path(ctx: ParseContext, raw: str | None) -> str | None:
    """Tool-reported path -> repo-relative POSIX path; None if outside repo/excluded/ignored."""
    if not raw:
        return None
    try:
        p = Path(raw)
        full = safe_resolve(ctx.root, p if not p.is_absolute() else p.relative_to(ctx.root))
        rel = full.relative_to(ctx.root).as_posix()
    except (PathSecurityError, ValueError, OSError):
        return None
    if set(rel.split("/")[:-1]) & ctx.excluded_dirs or ctx.ignore.match_file(rel):
        return None
    return rel


def _evidence(ctx: ParseContext, path: str, line: int | None, fallback: str = "") -> str:
    if fallback:
        return redact(fallback.strip())[:400]
    if line and ctx.reader.normalize(path):
        lines = ctx.reader.raw_lines(path)
        if 1 <= line <= len(lines):
            return redact(lines[line - 1].strip())[:300]
    return ""


def make_finding(ctx: ParseContext, tool: str, rule: str, area: str, severity: Severity, message: str,
                 raw_path: str | None, line: int | None, fix: str = "", evidence: str = "",
                 description: str = "", require_file: bool = True) -> Finding | None:
    path = rel_path(ctx, raw_path)
    if path is None and (require_file or raw_path):
        return None
    msg = redact(" ".join(message.split()))
    if line is not None and line < 1:
        line = None
    return Finding(
        id="tmp", agent=f"tool:{tool}", category=f"{area}/{tool}-{rule}".lower(), severity=severity,
        title=f"{rule}: {msg}"[:200], description=redact(description or f"Reported by {tool} (rule {rule}): {msg}")[:1500],
        file_path=path, line=line, evidence=_evidence(ctx, path, line, evidence) if path else redact(evidence)[:400],
        suggested_fix=fix, confidence=TOOL_CONFIDENCE, verification=Verification.VERIFIED,
    )


def _load(stdout: str, tool: str):
    try:
        return json.loads(stdout)
    except ValueError as exc:
        raise ToolOutputError(f"{tool} produced output that is not valid JSON") from exc


def _cap(ctx: ParseContext, items: list[Finding]) -> list[Finding]:
    items.sort(key=lambda f: list(Severity).index(f.severity))
    ctx.omitted = max(0, len(items) - MAX_FINDINGS_PER_TOOL)
    return items[:MAX_FINDINGS_PER_TOOL]


# ------------------------------------------------------------------ Ruff
_RUFF_HIGH = ("E9", "F63", "F7", "F82")
_RUFF_MEDIUM = {"B006", "B023", "F811"}


def ruff_severity(code: str) -> Severity:
    if code.startswith(_RUFF_HIGH):
        return Severity.HIGH
    return Severity.MEDIUM if code in _RUFF_MEDIUM else Severity.LOW


def parse_ruff(stdout: str, ctx: ParseContext) -> list[Finding]:
    data = _load(stdout or "[]", "ruff")
    if not isinstance(data, list):
        raise ToolOutputError("ruff JSON has unexpected shape")
    out = []
    for item in data:
        code = item.get("code") or "syntax"
        f = make_finding(ctx, "ruff", code, "quality", ruff_severity(code), item.get("message", ""),
                         item.get("filename"), (item.get("location") or {}).get("row"),
                         fix=(item.get("fix") or {}).get("message") or "")
        if f:
            out.append(f)
    return _cap(ctx, out)


# ------------------------------------------------------------------ Bandit
_BANDIT_SEV = {"HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW}


def parse_bandit(stdout: str, ctx: ParseContext) -> list[Finding]:
    data = _load(stdout or "{}", "bandit")
    if not isinstance(data, dict):
        raise ToolOutputError("bandit JSON has unexpected shape")
    out = []
    for item in data.get("results", []):
        rule = item.get("test_id", "B000")
        snippet = (item.get("code") or "").strip().split("\n")
        f = make_finding(ctx, "bandit", rule, "security", _BANDIT_SEV.get(item.get("issue_severity", ""), Severity.LOW),
                         item.get("issue_text", ""), item.get("filename"), item.get("line_number"),
                         fix=f"See {item.get('more_info')}" if item.get("more_info") else "",
                         description=f"Bandit {rule} ({item.get('test_name', '')}), "
                                     f"confidence {item.get('issue_confidence', '?')}: {item.get('issue_text', '')}")
        if f:
            if not f.evidence and snippet:
                f.evidence = redact(snippet[0])[:300]
            out.append(f)
    return _cap(ctx, out)


# ------------------------------------------------------------------ Gitleaks
def parse_gitleaks(stdout: str, ctx: ParseContext) -> list[Finding]:
    """Gitleaks must be run with --redact; values are re-redacted here regardless."""
    data = _load(stdout or "[]", "gitleaks") or []
    if not isinstance(data, list):
        raise ToolOutputError("gitleaks JSON has unexpected shape")
    out = []
    for item in data:
        raw = item.get("File")
        path = rel_path(ctx, raw)
        if path is None:
            continue
        sev = Severity.LOW if classify(path) == "test" else Severity.CRITICAL
        rule = item.get("RuleID", "secret")
        f = make_finding(ctx, "gitleaks", rule, "security", sev,
                         f"{item.get('Description', 'Secret detected')} (value redacted)", raw, item.get("StartLine"),
                         fix="Revoke and rotate this credential, remove it from the repository (and history), "
                             "and load it from an environment variable or secret store.",
                         evidence=item.get("Match") or "[REDACTED]")
        if f:
            f.evidence = "[REDACTED]" if not f.evidence else f.evidence
            out.append(f)
    return _cap(ctx, out)


# ------------------------------------------------------------------ ESLint
def parse_eslint(stdout: str, ctx: ParseContext) -> list[Finding]:
    data = _load(stdout or "[]", "eslint")
    if not isinstance(data, list):
        raise ToolOutputError("eslint JSON has unexpected shape")
    out = []
    for file in data:
        for m in file.get("messages", []):
            sev = Severity.MEDIUM if m.get("fatal") or m.get("severity") == 2 else Severity.LOW
            rule = m.get("ruleId") or ("parse-error" if m.get("fatal") else "eslint")
            f = make_finding(ctx, "eslint", rule, "quality", sev, m.get("message", ""), file.get("filePath"),
                             m.get("line"), fix="Auto-fixable: review the change, then apply with `eslint --fix`." if m.get("fix") else "")
            if f:
                out.append(f)
    return _cap(ctx, out)


# ------------------------------------------------------------------ TypeScript
_TSC = re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+(?P<kind>error|warning)\s+(?P<code>TS\d+):\s+(?P<msg>.*)$")


def parse_tsc(stdout: str, ctx: ParseContext) -> list[Finding]:
    out = []
    for line in stdout.splitlines():
        m = _TSC.match(line.strip())
        if not m:
            continue
        code = m["code"]
        sev = Severity.HIGH if code.startswith("TS1") else Severity.MEDIUM
        f = make_finding(ctx, "tsc", code, "quality", sev, m["msg"], m["file"], int(m["line"]))
        if f:
            out.append(f)
    return _cap(ctx, out)


# ------------------------------------------------------------------ npm audit
_NPM_SEV = {"critical": Severity.CRITICAL, "high": Severity.HIGH, "moderate": Severity.MEDIUM,
            "low": Severity.LOW, "info": Severity.INFO}


def parse_npm_audit(stdout: str, ctx: ParseContext) -> list[Finding]:
    data = _load(stdout or "{}", "npm audit")
    if not isinstance(data, dict):
        raise ToolOutputError("npm audit JSON has unexpected shape")
    if "error" in data and "vulnerabilities" not in data:
        err = data["error"]
        raise ToolOutputError(f"npm audit error: {err.get('summary') or err.get('code') or err}"[:300])
    manifest = "package.json" if ctx.reader.normalize("package.json") else None
    out = []
    for name, v in (data.get("vulnerabilities") or {}).items():
        via = [x if isinstance(x, str) else x.get("title", "") for x in v.get("via", [])]
        via = [x for x in via if x][:3]
        fa = v.get("fixAvailable")
        if isinstance(fa, dict):
            fix = f"Upgrade {fa.get('name')} to {fa.get('version')}" + (" (breaking change)" if fa.get("isSemVerMajor") else "")
        else:
            fix = "Run `npm audit fix`." if fa else "No fix available yet; consider an alternative package."
        direct = " (direct dependency)" if v.get("isDirect") else " (transitive)"
        f = make_finding(ctx, "npm-audit", name, "security", _NPM_SEV.get(v.get("severity", ""), Severity.LOW),
                         f"{v.get('severity', 'unknown')} vulnerability in {name}{direct}", manifest, None, fix=fix,
                         evidence=f"{name} {v.get('range', '')}; advisories: {'; '.join(via) or 'n/a'}",
                         require_file=False)
        if f:
            out.append(f)
    return _cap(ctx, out)
