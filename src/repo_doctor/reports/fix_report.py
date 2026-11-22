from __future__ import annotations

from datetime import UTC, datetime

from repo_doctor.core.fixer import FixSession
from repo_doctor.security.redaction import redact

FIX_REPORT_NAME = "FIX_REPORT.md"
NL = "\n"
ICON = {"applied": "applied", "applied-unverified": "applied (unverified)", "rolled-back": "ROLLED BACK",
        "skipped": "skipped", "declined": "declined", "dry-run": "dry run"}


def render_fix_report(session: FixSession) -> str:
    ok = session.count("applied") + session.count("applied-unverified")
    rows = [f"| {r.finding_id} | `{r.file_path or '-'}` | {ICON.get(r.outcome, r.outcome)} | "
            f"{r.detail.replace('|', '/').replace(NL, ' ')} |" for r in session.records]
    parts = [
        f"# Fix Report\n\n_Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} · repository `{session.root}`_\n",
        "## Summary\n",
        f"- Applied and verified: **{session.count('applied')}**",
        f"- Applied, no conclusive verification: **{session.count('applied-unverified')}**",
        f"- Rolled back after failed verification: **{session.count('rolled-back')}**",
        f"- Declined by user: {session.count('declined')} · Skipped (not fixable/invalid): {session.count('skipped')}"
        + (f" · Dry-run proposals: {session.count('dry-run')}" if session.dry_run else ""),
        f"- Original files backed up to: `{session.backup_dir}`" if ok or session.count("rolled-back") else
        "- No files were modified.",
        "- Review all changes with `git diff` (if the repository uses git) before committing.\n",
        "## Results\n",
        "| Finding | File | Outcome | Detail |", "|---|---|---|---|", *rows, "",
    ]
    for r in session.records:
        if r.outcome in ("applied", "applied-unverified", "rolled-back", "dry-run") and r.diff:
            parts += [f"### {r.finding_id}: {r.title}", "", f"Outcome: **{ICON.get(r.outcome, r.outcome)}**", ""]
            if r.checks:
                parts += ["Verification:", ""] + [f"- `{c['name']}`: **{c['status']}** — {c['detail']}" for c in r.checks] + [""]
            parts += ["```diff", r.diff.replace("```", "'''").rstrip(), "```", ""]
    return redact(NL.join(parts))
