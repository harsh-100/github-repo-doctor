"""Safe repository scanner: builds an inventory without reading file contents."""
from __future__ import annotations

import os
from pathlib import Path

import pathspec

from repo_doctor.core.models import FileInfo, Inventory
from repo_doctor.core.settings import ScanSettings
from repo_doctor.security.permissions import is_sensitive_file

from .language import LOCKFILES, classify, detect_language

DEFAULT_EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next",
    "coverage", "vendor", ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".idea", ".vscode", "reports",
}
GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".bundle.js")


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\0" in fh.read(2048)
    except OSError:
        return True


def load_ignore_spec(root: Path, extra: list[str]) -> pathspec.PathSpec:
    lines: list[str] = list(extra)
    gi = root / ".gitignore"
    if gi.is_file():
        try:
            lines += gi.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            pass
    return pathspec.PathSpec.from_lines("gitignore", lines)


def scan_repository(root: Path, settings: ScanSettings | None = None) -> Inventory:
    """Walk `root` and return an Inventory. Never follows symlinks out of the repo."""
    settings = settings or ScanSettings()
    root = root.resolve()
    spec = load_ignore_spec(root, settings.extra_excludes)
    max_bytes = settings.max_file_size_kb * 1024
    inv = Inventory(root=str(root))

    def skip(reason: str) -> None:
        inv.skipped[reason] = inv.skipped.get(reason, 0) + 1

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root)
        kept = []
        for d in sorted(dirnames):
            rel = (rel_dir / d).as_posix()
            if d in DEFAULT_EXCLUDED_DIRS:
                skip("excluded directory")
            elif (rel_dir / d).is_symlink():
                skip("symlink")
            elif spec.match_file(rel + "/"):
                skip("gitignored")
            else:
                kept.append(d)
        dirnames[:] = kept

        for fname in sorted(filenames):
            full = Path(dirpath) / fname
            rel = (rel_dir / fname).as_posix()
            if full.is_symlink():
                skip("symlink")
            elif is_sensitive_file(fname):
                skip("sensitive file")
                if not spec.match_file(rel):
                    inv.sensitive_files.append(rel)  # name only; contents never read
            elif spec.match_file(rel):
                skip("gitignored")
            elif fname.lower() in LOCKFILES:
                skip("lockfile")
            elif fname.lower().endswith(GENERATED_SUFFIXES):
                skip("generated file")
            else:
                try:
                    size = full.stat().st_size
                except OSError:
                    skip("unreadable")
                    continue
                if size > max_bytes:
                    skip("too large")
                elif _looks_binary(full):
                    skip("binary")
                elif len(inv.files) >= settings.max_files:
                    inv.truncated = True
                    skip("file limit reached")
                else:
                    inv.files.append(
                        FileInfo(path=rel, size=size, language=detect_language(rel), kind=classify(rel))
                    )
    return inv
