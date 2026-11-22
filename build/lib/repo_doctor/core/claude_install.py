"""Install the Claude Code slash commands, subagent and permissions into a project's .claude/ folder."""
from __future__ import annotations

import json
import shutil
from importlib.resources import files
from pathlib import Path


class InstallError(Exception):
    pass


def _source():
    return files("repo_doctor") / "claude_code"


def _merge_permissions(existing: dict, ours: dict) -> dict:
    merged = dict(existing)
    perms = dict(merged.get("permissions") or {})
    for kind, rules in (ours.get("permissions") or {}).items():
        current = list(perms.get(kind) or [])
        perms[kind] = current + [r for r in rules if r not in current]
    merged["permissions"] = perms
    return merged


def install(target: Path, force: bool = False) -> list[str]:
    """Copy templates into <target>/.claude. Never overwrites a changed file without `force`;
    merges permissions into an existing settings.json (keeping a .bak) and never touches invalid JSON."""
    src = _source()
    dest = target / ".claude"
    actions: list[str] = []

    settings_path = dest / "settings.json"
    ours = json.loads((src / "settings.permissions.json").read_text(encoding="utf-8"))
    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                raise TypeError("not a JSON object")
        except (ValueError, TypeError) as exc:
            raise InstallError(f"{settings_path} is not valid JSON ({exc}); fix it or merge "
                               "settings.permissions.json from the package by hand. Nothing was changed.") from exc

    for kind in ("commands", "agents"):
        for item in sorted((src / kind).iterdir(), key=lambda p: p.name):
            if not item.name.endswith(".md"):
                continue
            out = dest / kind / item.name
            text = item.read_text(encoding="utf-8")
            if out.exists():
                if out.read_text(encoding="utf-8") == text:
                    actions.append(f"unchanged  {out.relative_to(target)}")
                    continue
                if not force:
                    actions.append(f"SKIPPED    {out.relative_to(target)} (differs; use --force to overwrite)")
                    continue
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            actions.append(f"wrote      {out.relative_to(target)}")

    merged = _merge_permissions(existing, ours)
    if merged != existing or not settings_path.exists():
        dest.mkdir(parents=True, exist_ok=True)
        if settings_path.exists():
            shutil.copy2(settings_path, settings_path.with_suffix(".json.bak"))
        settings_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        actions.append(f"updated    {settings_path.relative_to(target)} (permissions merged)")
    else:
        actions.append(f"unchanged  {settings_path.relative_to(target)}")
    return actions
