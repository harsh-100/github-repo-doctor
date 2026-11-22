"""Build bounded, redacted context for LLM prompts (never the whole repo)."""
from __future__ import annotations

from pathlib import Path

from repo_doctor.core.models import ProjectProfile
from repo_doctor.security.permissions import is_sensitive_file, safe_resolve
from repo_doctor.security.redaction import redact

MAX_CHARS_PER_FILE = 3000
MAX_TOTAL_CHARS = 14000


def read_excerpt(root: Path, rel: str, limit: int = MAX_CHARS_PER_FILE) -> str:
    if is_sensitive_file(rel):
        return ""
    try:
        text = safe_resolve(root, rel).read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return ""
    text = redact(text)
    return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")


def build_overview_context(root: Path, profile: ProjectProfile) -> str:
    """Summary of the profile plus excerpts of README, manifests and entry points."""
    parts = [
        f"Project: {profile.name}",
        f"Languages (file counts): {profile.languages}",
        f"Frameworks: {profile.frameworks}",
        f"Top-level directories (file counts): {profile.top_level_modules}",
        f"Entry points: {profile.entry_points}",
        f"Test frameworks: {profile.test_frameworks}",
    ]
    inv_paths = [f.path for f in profile.inventory.files]
    readmes = [p for p in inv_paths if Path(p).name.lower().startswith("readme")][:1]
    entries = [e for e in profile.entry_points if e in set(inv_paths)][:3]
    total = sum(len(p) for p in parts)
    for rel in readmes + profile.manifests[:4] + entries:
        excerpt = read_excerpt(root, rel)
        if not excerpt or total + len(excerpt) > MAX_TOTAL_CHARS:
            continue
        total += len(excerpt)
        parts.append(f"\n--- {rel} ---\n{excerpt}")
    return "\n".join(parts)
