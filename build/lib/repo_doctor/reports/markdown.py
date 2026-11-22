from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from repo_doctor.core.models import ProjectProfile
from repo_doctor.security.redaction import redact

OVERVIEW_NAME = "PROJECT_OVERVIEW.md"
HEALTH_NAME = "REPO_HEALTH_REPORT.md"


def _bullets(items, empty="_None detected_") -> str:
    items = list(items)
    return "\n".join(f"- {i}" for i in items) if items else empty


def _tree(profile: ProjectProfile, max_dirs: int = 25) -> str:
    inv = profile.inventory
    lines = [f"{profile.name}/"]
    for d, n in list(profile.top_level_modules.items())[:max_dirs]:
        lines.append(f"├── {d}/  ({n} files)")
    root_files = [f.path for f in inv.files if "/" not in f.path][:25]
    lines += [f"├── {p}" for p in root_files]
    return "```text\n" + "\n".join(lines) + "\n```"


def render_overview(profile: ProjectProfile, narrative: str | None = None) -> str:
    inv = profile.inventory
    langs = ", ".join(f"{k} ({v})" for k, v in profile.languages.items()) or "_None detected_"
    prod = [d for d in profile.dependencies if not d.dev]
    dev = [d for d in profile.dependencies if d.dev]

    def dep_lines(ds):
        return _bullets(f"`{d.name}` {d.spec}".strip() + f" _({d.ecosystem})_" for d in ds[:40])

    skipped = ", ".join(f"{k}: {v}" for k, v in inv.skipped.items()) or "nothing"
    important = [f.path for f in inv.files if f.kind in {"manifest", "build"}][:20]
    docs = profile.docs
    narrative_block = narrative if narrative else (
        "_No AI summary generated (no provider configured or `--no-llm` used). "
        "Sections below are derived from deterministic analysis only._"
    )
    out = f"""# Project Overview: {profile.name}

_Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by GitHub Repo Doctor_

## Project description & architecture

{narrative_block}

## Detected technologies

- **Languages (files):** {langs}
- **Frameworks:** {", ".join(profile.frameworks) or "_None detected_"}
- **Package managers:** {", ".join(profile.package_managers) or "_None detected_"}
- **Test frameworks:** {", ".join(profile.test_frameworks) or "_None detected_"}

## Directory structure

{_tree(profile)}

Scanned {len(inv.files)} files. Skipped: {skipped}.

## Important files

{_bullets(f"`{p}`" for p in important)}

## Application entry points

{_bullets(f"`{p}`" for p in profile.entry_points)}

## Dependencies

**Runtime ({len(prod)})**

{dep_lines(prod)}

**Development / optional ({len(dev)})**

{dep_lines(dev)}

## Existing documentation

{_bullets(f"`{p}`" for p in docs)}

## Potential technical concerns

{_bullets(profile.concerns, "_None found by deterministic checks_")}
"""
    return redact(out)


def write_report(content: str, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(content, encoding="utf-8")
    return path
