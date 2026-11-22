"""Fix workflow core: propose minimal edits, validate them, back up, apply atomically, roll back.

Safety invariants (see tests):
  * nothing is written unless the caller's `approve` callback returns True for that exact proposal;
  * the model only ever sees REDACTED text; edits must match the real file exactly once, inside the
    window the model saw, and may not touch or add secret-like values;
  * originals are copied outside the repository before the first write to a file;
  * every write is atomic and symlinks are never written through.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import itertools
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from repo_doctor.agents.base import extract_json, locate_evidence
from repo_doctor.core.models import AnalysisResult, Finding, Severity, Verification
from repo_doctor.core.settings import config_dir
from repo_doctor.providers import LLMProvider, ProviderError
from repo_doctor.security.permissions import PathSecurityError, is_sensitive_file, safe_resolve
from repo_doctor.security.redaction import REDACTED, contains_secret, redact, redact_lines

WINDOW = 40            # lines of context either side of the finding shown to the model
MAX_EDITS = 3
MAX_EDIT_LINES = 60    # per search/replace block
MAX_SELECTED = 20

SYSTEM = (
    "You are a careful engineer making the SMALLEST possible code change to resolve ONE reported finding. "
    "Reply with ONLY a JSON object: "
    '{"edits": [{"search": "<existing lines copied verbatim, WITHOUT the line-number prefixes>", '
    '"replace": "<replacement lines>", "reason": "<why this change resolves the finding>"}], '
    '"cannot_fix": "<reason, only if no safe minimal fix exists>"}. '
    "Rules: `search` must match the file exactly and be unique (include enough surrounding lines); keep the "
    f"existing indentation; do not reformat unrelated code; at most {MAX_EDITS} edits; never add credentials "
    "or secrets; text shown as [REDACTED] cannot be edited. If unsure, set cannot_fix and return no edits."
)

_UNFIXABLE_CATEGORY = (
    ("security/hardcoded-secret", "contains a credential: rotate/revoke it first, then remove it by hand"),
    ("security/gitleaks", "contains a credential: rotate/revoke it first, then remove it by hand"),
    ("security/npm-audit", "dependency vulnerability: upgrade the package (needs lockfile/network changes)"),
    ("testing/", "requires writing new test files, which `fix` does not do"),
)


class Edit(BaseModel):
    search: str
    replace: str
    reason: str = ""


class Proposal(BaseModel):
    finding: Finding
    rel: str
    old_text: str = Field(repr=False)
    new_text: str = Field(repr=False)
    edits: list[Edit]
    diff: str  # redacted, for display only


class Skip(Exception):
    """Raised when a finding cannot (or should not) be fixed; message is shown to the user."""


# ------------------------------------------------------------------ eligibility
def unfixable_reason(f: Finding) -> str | None:
    if not f.file_path:
        return "project-level finding (no file to edit)"
    if f.line is None:
        return "no line number recorded"
    if f.verification in (Verification.SPECULATIVE, Verification.REJECTED):
        return f"{f.verification.value} finding"
    if f.severity == Severity.INFO:
        return "informational"
    for prefix, why in _UNFIXABLE_CATEGORY:
        if f.category.startswith(prefix):
            return why
    if is_sensitive_file(f.file_path):
        return "sensitive file"
    if REDACTED in f.evidence or contains_secret(f.evidence):
        return "line contains a secret-like value: fix by hand"
    return None


def load_findings(path: Path) -> AnalysisResult:
    """Read findings from a run's findings.json (ReportData; the older bare AnalysisResult is also accepted)."""
    if path.is_dir():
        path = path / "findings.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `repo-doctor analyze` first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AnalysisResult.model_validate(data["result"] if isinstance(data, dict) and "result" in data else data)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{path} is not a valid findings file: {exc}") from exc


# ------------------------------------------------------------------ file access
def read_text_file(root: Path, rel: str) -> str:
    try:
        full = safe_resolve(root, rel)
    except PathSecurityError as exc:
        raise Skip(str(exc)) from exc
    if (root / rel).is_symlink() or full.is_symlink():
        raise Skip("file is a symlink; refusing to edit through it")
    if not full.is_file():
        raise Skip("file no longer exists")
    try:
        return full.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Skip("file is not valid UTF-8") from exc


def atomic_write(root: Path, rel: str, text: str) -> None:
    full = safe_resolve(root, rel)
    if full.is_symlink():
        raise PathSecurityError("refusing to write through a symlink")
    mode = full.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(dir=full.parent, prefix=".repo-doctor-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.chmod(tmp, mode)
        os.replace(tmp, full)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Backups:
    """Originals are copied OUTSIDE the repository before the first modification."""

    def __init__(self, root: Path, base: Path | None = None):
        tag = hashlib.sha1(str(root).encode()).hexdigest()[:8]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.dir = (base or config_dir() / "backups") / f"{root.name}-{tag}" / stamp
        self.root = root
        self.saved: dict[str, str] = {}  # rel -> sha256 of original

    def save(self, rel: str, original: str) -> None:
        if rel in self.saved:
            return
        dest = self.dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(original.encode("utf-8"))
        shutil.copystat(safe_resolve(self.root, rel), dest)
        self.saved[rel] = hashlib.sha256(original.encode("utf-8")).hexdigest()

    def original(self, rel: str) -> str:
        return (self.dir / rel).read_bytes().decode("utf-8")

    def restore_to(self, rel: str, text: str) -> None:
        atomic_write(self.root, rel, text)


# ------------------------------------------------------------------ proposal generation
def _window(lines: list[str], line: int) -> tuple[int, int]:
    return max(1, line - WINDOW), min(len(lines), line + WINDOW)


def _locate(f: Finding, lines: list[str]) -> int:
    """Re-locate the finding in the CURRENT file; refuse if it has moved/changed (stale report)."""
    assert f.line is not None
    if f.evidence:
        found = locate_evidence(lines, f.evidence, f.line)
        if found is None:
            raise Skip("evidence no longer found in the file (it changed since the analysis; re-run analyze)")
        return found
    if not 1 <= f.line <= len(lines):
        raise Skip("line number is out of range (file changed since the analysis)")
    return f.line


def _nl(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _apply_edits(text: str, edits: list[Edit], lo: int, hi: int) -> str:
    nl = _nl(text)
    spans: list[tuple[int, int, str]] = []
    for e in edits:
        search = e.search.replace("\r\n", "\n").replace("\n", nl)
        replace = e.replace.replace("\r\n", "\n").replace("\n", nl)
        if not search.strip():
            raise Skip("model returned an empty search block")
        if REDACTED in search or REDACTED in replace:
            raise Skip("edit touches redacted text; fix by hand")
        if text.count(search) != 1:
            raise Skip(f"search block matched {text.count(search)} times (must be exactly once); edit rejected")
        if max(search.count(nl), replace.count(nl)) + 1 > MAX_EDIT_LINES:
            raise Skip(f"edit is too large (> {MAX_EDIT_LINES} lines); make it by hand")
        if search == replace:
            raise Skip("edit makes no change")
        pos = text.index(search)
        start_line = text.count(nl, 0, pos) + 1
        end_line = start_line + search.count(nl)
        if start_line < lo or end_line > hi:
            raise Skip("edit is outside the code that was shown to the model")
        if contains_secret(replace) and not contains_secret(search):
            raise Skip("edit would introduce a secret-like value")
        spans.append((pos, pos + len(search), replace))
    spans.sort()
    for (_, e1, _), (s2, _, _) in itertools.pairwise(spans):
        if s2 < e1:
            raise Skip("edits overlap")
    for s, e, r in reversed(spans):
        text = text[:s] + r + text[e:]
    return text


def syntax_error(rel: str, text: str) -> str | None:
    if rel.endswith(".py"):
        try:
            ast.parse(text)
        except (SyntaxError, ValueError) as exc:
            return f"{type(exc).__name__}: {exc}"
    elif rel.endswith(".json"):
        try:
            json.loads(text)
        except ValueError as exc:
            return f"invalid JSON: {exc}"
    return None


def diff_id(prop: Proposal) -> str:
    """Stable id of the exact change (file + before + after). Approval can be bound to it."""
    h = hashlib.sha256()
    for part in (prop.rel, prop.old_text, prop.new_text):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def make_diff(rel: str, old: str, new: str) -> str:
    diff = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3)
    return "".join(redact_lines(line) for line in diff)  # unchanged context could hold secrets


def propose_fix(provider: LLMProvider, root: Path, f: Finding) -> Proposal:
    """Ask the model for a minimal fix and validate it. Raises Skip with a reason otherwise."""
    reason = unfixable_reason(f)
    if reason:
        raise Skip(reason)
    rel = f.file_path or ""
    old = read_text_file(root, rel)
    lines = old.split("\n")
    centre = _locate(f, lines)
    lo, hi = _window(lines, centre)
    shown = redact_lines(old).split("\n")
    listing = "\n".join(f"{i:>5}| {shown[i - 1][:400]}" for i in range(lo, hi + 1))
    brief = {k: redact(str(v)) for k, v in {
        "id": f.id, "title": f.title, "severity": f.severity.value, "file": rel, "line": centre,
        "description": f.description[:600], "evidence": f.evidence[:300],
        "suggested_fix": f.suggested_fix[:400], "tool_explanation": f.explanation[:400]}.items()}
    prompt = (f"Finding to resolve:\n{json.dumps(brief, indent=1)}\n\n"
              f"Code from {rel} (lines {lo}-{hi}):\n{listing}")

    data = None
    for attempt in range(2):
        retry = "" if attempt == 0 else "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
        try:
            data = extract_json(provider.complete(SYSTEM, prompt + retry))
        except ProviderError as exc:
            raise Skip(f"model request failed: {exc}") from exc
        if data is not None:
            break
    if data is None:
        raise Skip("model did not return valid JSON")
    raw_edits = data.get("edits")
    if not isinstance(raw_edits, list) or not raw_edits:
        why = redact(str(data.get("cannot_fix") or "model proposed no edits"))[:300]
        raise Skip(f"no automatic fix: {why}")
    if len(raw_edits) > MAX_EDITS:
        raise Skip(f"model proposed {len(raw_edits)} edits (max {MAX_EDITS}); fix by hand")
    try:
        edits = [Edit(search=str(e["search"]), replace=str(e["replace"]), reason=str(e.get("reason", ""))[:400])
                 for e in raw_edits]
    except (KeyError, TypeError) as exc:
        raise Skip("model returned malformed edits") from exc

    new = _apply_edits(old, edits, lo, hi)
    if new == old:
        raise Skip("proposed change is empty")
    if syntax_error(rel, old) is None and (err := syntax_error(rel, new)):
        raise Skip(f"proposed change would break syntax ({err}); rejected")
    return Proposal(finding=f, rel=rel, old_text=old, new_text=new, edits=edits, diff=make_diff(rel, old, new))


# ------------------------------------------------------------------ session
class FixRecord(BaseModel):
    finding_id: str
    title: str
    file_path: str | None
    outcome: str  # applied | applied-unverified | rolled-back | skipped | declined | dry-run
    detail: str = ""
    diff: str = ""
    checks: list[dict] = Field(default_factory=list)


class FixSession(BaseModel):
    root: str
    started: str
    dry_run: bool = False
    backup_dir: str = ""
    records: list[FixRecord] = Field(default_factory=list)

    def count(self, outcome: str) -> int:
        return len([r for r in self.records if r.outcome == outcome])


class VerifierLike(Protocol):  # implemented in core/verify.py
    def prepare(self, f: Finding, rel: str, old_text: str) -> None: ...
    def check(self, f: Finding, rel: str, new_text: str) -> tuple[bool, list[dict]]: ...
    def discard(self, f: Finding, rel: str) -> None: ...


def _noop_show(prop: Proposal) -> None:
    return None


def _noop_notify(kind: str, msg: str) -> None:
    return None


@dataclass
class Hooks:
    approve: Callable[[Proposal], bool]  # only called when not a dry run; must return True to apply
    show: Callable[[Proposal], None] = _noop_show        # display the proposal (always called)
    notify: Callable[[str, str], None] = _noop_notify    # (kind, message) progress/outcome events


def run_fix_session(root: Path, provider: LLMProvider, findings: list[Finding], hooks: Hooks,
                    verifier: VerifierLike | None = None, dry_run: bool = False,
                    backup_base: Path | None = None) -> FixSession:
    root = root.resolve()
    session = FixSession(root=str(root), started=datetime.now(UTC).isoformat(), dry_run=dry_run)
    backups = Backups(root, backup_base)
    session.backup_dir = str(backups.dir)

    for f in findings[:MAX_SELECTED]:
        rec = FixRecord(finding_id=f.id, title=f.title, file_path=f.file_path, outcome="skipped")
        session.records.append(rec)
        try:
            prop = propose_fix(provider, root, f)
        except Skip as exc:
            rec.detail = str(exc)
            hooks.notify("skip", f"{f.id}: {exc}")
            continue
        rec.diff = prop.diff
        hooks.show(prop)
        if dry_run:
            rec.outcome, rec.detail = "dry-run", "proposed only; nothing was changed"
            continue
        if not hooks.approve(prop):
            rec.outcome, rec.detail = "declined", "not approved by user"
            continue

        # ---- approved: back up, apply, verify, roll back on failure
        try:
            current = read_text_file(root, prop.rel)
            if current != prop.old_text:
                raise Skip("file changed while waiting for approval; not applied")
            if verifier:
                verifier.prepare(f, prop.rel, prop.old_text)
            backups.save(prop.rel, prop.old_text)
            atomic_write(root, prop.rel, prop.new_text)
        except (Skip, PathSecurityError, OSError) as exc:
            rec.detail = f"not applied: {exc}"
            hooks.notify("skip", f"{f.id}: {rec.detail}")
            continue

        ok, checks = (True, [])
        if verifier:
            try:
                ok, checks = verifier.check(f, prop.rel, prop.new_text)
            except Exception as exc:  # noqa: BLE001 - verification crash must roll back, not leave a bad edit
                ok, checks = False, [{"name": "verification", "status": "fail", "detail": f"crashed: {exc}"}]
        rec.checks = checks
        if ok:
            conclusive = any(c["status"] == "pass" for c in checks)
            rec.outcome = "applied" if conclusive else "applied-unverified"
            rec.detail = "applied; verification passed" if conclusive else "applied; no conclusive verification available"
        else:
            atomic_write(root, prop.rel, prop.old_text)
            if read_text_file(root, prop.rel) != prop.old_text:
                hooks.notify("error", f"{f.id}: rollback did not restore the file; copy it back from {backups.dir}")
            if verifier:
                verifier.discard(f, prop.rel)
            rec.outcome = "rolled-back"
            failed = "; ".join(f"{c['name']}: {c['detail']}" for c in checks if c["status"] == "fail")
            rec.detail = f"verification failed, change rolled back ({failed})"
        hooks.notify(rec.outcome, f"{f.id}: {rec.detail}")
    return session


_ID = re.compile(r"^[A-Za-z]-\d+$")


def select_findings(result: AnalysisResult, spec: list[str], min_severity: Severity | None = None
                    ) -> tuple[list[Finding], list[tuple[Finding, str]]]:
    """Return (fixable, unfixable-with-reason). `spec` = explicit ids (empty = all fixable)."""
    order = list(Severity)
    fixable: list[Finding] = []
    unfixable: list[tuple[Finding, str]] = []
    wanted = {s.upper() for s in spec}
    for f in result.findings:
        if wanted and f.id.upper() not in wanted:
            continue
        if min_severity and order.index(f.severity) > order.index(min_severity):
            continue
        why = unfixable_reason(f)
        (unfixable.append((f, why)) if why else fixable.append(f))
    return fixable, unfixable
