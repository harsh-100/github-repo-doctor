"""Security: deterministic secret/risk scan + LLM review of risky files."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from repo_doctor.core.models import Finding, Severity, Verification
from repo_doctor.providers import ProviderError
from repo_doctor.security.redaction import REDACTED, TOKEN_PATTERNS, redact

from .base import JSON_FORMAT, Agent, AgentContext, ask_findings, build_batches

SYSTEM = (
    "You are an application-security reviewer. Find exploitable or dangerous patterns: injection "
    "(SQL, command, template), unsafe deserialization, missing authentication/validation, insecure "
    "configuration, OWASP Top 10 issues. Secret values appear as [REDACTED]; never try to reconstruct "
    "them. Use category 'security/<subtype>' and include a concrete remediation."
)

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_HARDCODED = re.compile(
    r"""(?ix)\b[\w.\-]*(?:password|passwd|secret|token|api[_\-]?key|apikey|access[_\-]?key)[\w.\-]*
        ["']?\s*[:=]\s*["'](?P<val>[^"'\s]{8,})["']""")
_URL_CREDS = re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@]+:(?P<pw>[^/\s@$<{]{3,})@")
_PLACEHOLDER = re.compile(r"(?i)example|your[_\-]|xxx|changeme|placeholder|dummy|sample|<|\$\{|%\(|\{\{|\*\*\*")

# (regex, languages, severity, title, description, fix)
RISKY: list[tuple[re.Pattern, set[str], Severity, str, str, str]] = [
    (re.compile(r"\beval\s*\("), {"Python", "JavaScript", "TypeScript"}, Severity.MEDIUM,
     "Use of eval()", "eval() executes arbitrary code; with untrusted input this is code injection.",
     "Replace eval with a safe parser (e.g. ast.literal_eval / JSON.parse)."),
    (re.compile(r"\bexec\s*\("), {"Python"}, Severity.MEDIUM, "Use of exec()",
     "exec() runs dynamically built code.", "Avoid exec; use explicit dispatch."),
    (re.compile(r"\bos\.system\s*\("), {"Python"}, Severity.MEDIUM, "Shell command via os.system",
     "os.system runs through a shell and is injection-prone with variable input.",
     "Use subprocess.run([...]) with an argument list and no shell."),
    (re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), {"Python"}, Severity.MEDIUM,
     "subprocess with shell=True", "shell=True makes command injection possible with variable input.",
     "Pass an argument list and drop shell=True."),
    (re.compile(r"\bpickle\.loads?\s*\("), {"Python"}, Severity.MEDIUM, "Unsafe deserialization (pickle)",
     "Unpickling untrusted data can execute arbitrary code.", "Use JSON or another data-only format."),
    (re.compile(r"\.execute\(\s*f[\"']|\.execute\([^)]*[\"']\s*%\s*|\.execute\([^)]*[\"']\s*\+"), {"Python"},
     Severity.HIGH, "Possible SQL injection (string-built query)",
     "SQL appears to be built by string formatting/concatenation.",
     "Use parameterised queries (cursor.execute(sql, params))."),
    (re.compile(r"child_process|\bexecSync?\s*\("), {"JavaScript", "TypeScript"}, Severity.MEDIUM,
     "Command execution via child_process", "Spawning shell commands with variable input risks injection.",
     "Use execFile/spawn with an argument array and validate inputs."),
    (re.compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML"), {"JavaScript", "TypeScript"}, Severity.MEDIUM,
     "Raw HTML injection sink", "Writing unsanitised data to HTML enables XSS.",
     "Use textContent or sanitise (e.g. DOMPurify) before rendering."),
]
_NAME_HINT = re.compile(r"(?i)auth|login|user|admin|db|database|sql|query|api|route|server|view|upload|session|token|config|settings")


def _in_string(line: str, pos: int) -> bool:
    """Heuristic: is `pos` inside a quoted string? (odd number of quotes before it)"""
    before = line[:pos]
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1


def _snippet(line: str) -> str:
    return redact(line.strip())[:300]


def _f(path: str, line: int | None, sev: Severity, title: str, desc: str, evidence: str, fix: str,
       sub: str, conf: float = 0.9) -> Finding:
    return Finding(id="tmp", agent="security", category=f"security/{sub}", severity=sev, title=title,
                   description=desc, file_path=path, line=line, evidence=evidence, suggested_fix=fix,
                   confidence=conf, verification=Verification.VERIFIED)


def scan_file(path: str, language: str | None, kind: str, lines: list[str]) -> list[Finding]:
    out: list[Finding] = []
    is_test = kind == "test"
    for n, line in enumerate(lines, 1):
        if _PRIVATE_KEY.search(line):
            out.append(_f(path, n, Severity.LOW if is_test else Severity.CRITICAL, "Private key committed to repository",
                          "A private key block is present in a source file.", REDACTED,
                          "Remove it, rotate the key, and load keys from a secret store.", "hardcoded-secret"))
            continue
        if any(p.search(line) for p in TOKEN_PATTERNS):
            out.append(_f(path, n, Severity.LOW if is_test else Severity.CRITICAL, "API key or token in source",
                          "A string matching a known credential format is committed. (Value redacted.)",
                          _snippet(line), "Revoke/rotate the credential and load it from an environment variable.",
                          "hardcoded-secret"))
            continue
        m = _URL_CREDS.search(line)
        if m and not _PLACEHOLDER.search(m["pw"]):
            out.append(_f(path, n, Severity.LOW if is_test else Severity.HIGH, "Credentials embedded in URL",
                          "A connection string contains a username and password.", _snippet(line),
                          "Move credentials to environment variables or a secret manager.", "hardcoded-secret"))
            continue
        m = _HARDCODED.search(line)
        if m and not _PLACEHOLDER.search(m["val"]):
            out.append(_f(path, n, Severity.LOW if is_test else Severity.MEDIUM, "Hardcoded credential-like value",
                          "A variable that looks like a password/token/key is assigned a string literal.",
                          _snippet(line), "Read the value from an environment variable or secret store.",
                          "hardcoded-secret", conf=0.7))
    if language and not is_test:
        for n, line in enumerate(lines, 1):
            if line.lstrip().startswith(("#", "//", "*")):
                continue
            for rx, langs, sev, title, desc, fix in RISKY:
                m = rx.search(line) if language in langs else None
                if m and not _in_string(line, m.start()):
                    out.append(_f(path, n, sev, title, desc + " (Pattern match: exploitability depends on input source.)",
                                  _snippet(line), fix, "unsafe-pattern", conf=0.6))
    return out


class SecurityAgent(Agent):
    name = "security"
    commands = frozenset({"analyze", "security"})

    def run(self, ctx: AgentContext) -> list[Finding]:
        inv = ctx.profile.inventory
        findings: list[Finding] = []
        for rel in inv.sensitive_files:
            findings.append(_f(rel, None, Severity.HIGH, f"Sensitive file not gitignored: {PurePosixPath(rel).name}",
                               "A secrets-bearing file exists in the repository directory and is not matched by "
                               ".gitignore, so it may be committed. (Contents were not read.)",
                               f"{rel} present; no matching .gitignore rule",
                               f"Add `{PurePosixPath(rel).name}` to .gitignore; if already committed, rotate its secrets "
                               "and purge it from history.", "unsafe-config"))
        hits: dict[str, int] = {}
        for f in inv.files:
            if f.kind in {"source", "test", "config", "build", "manifest"} or f.language:
                found = scan_file(f.path, f.language, f.kind, ctx.reader.raw_lines(f.path))
                findings += found
                hits[f.path] = len(found)

        if not ctx.llm_enabled:
            return findings
        build = {f.path for f in inv.files if f.kind == "build"}
        cands = [f.path for f in inv.files if f.language and f.kind == "source"
                 and (hits.get(f.path) or _NAME_HINT.search(f.path))] + sorted(build)
        cands = sorted(dict.fromkeys(cands), key=lambda p: -hits.get(p, 0))
        if not cands:
            return findings
        known = "\n".join(f"- {x.file_path}:{x.line} {x.title}" for x in findings[:40]) or "(none)"
        batches, covered = build_batches(ctx, cands)
        if covered < len(cands):
            ctx.notes.append(f"security: LLM reviewed {covered} of {len(cands)} candidate files (call limit).")
        for body, sent in batches:
            prompt = (f"Already detected by pattern scan (do not repeat):\n{known}\n\n"
                      f"Review this code:\n\n{body}\n\n{JSON_FORMAT}")
            try:
                findings += ask_findings(ctx, self.name, "security", SYSTEM, prompt, sent)
            except ProviderError as exc:
                ctx.warnings.append(f"security: LLM batch failed: {exc}")
        return findings
