"""Report workspace: reports/<project>/runs/<run-id>/...

  reports/<project-slug>/project.json     stable identity + run list (rebuilt from the runs on disk)
  reports/<project-slug>/index.html       project history dashboard
  reports/<project-slug>/runs/<run-id>/   report.md, report.html, findings.json, metadata.json, assets/

Runs are never overwritten or deleted. The project slug comes from the folder name; two different
projects with the same folder name get distinct workspaces through a stable identity hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_RUN_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{6}$")


def slugify(name: str, limit: int = 60) -> str:
    """Safe, portable folder name: [a-z0-9._-], no leading dots/dashes, never empty, never reserved."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-._")[:limit].strip("-._")
    if not text:
        text = "project"
    if text in _RESERVED:
        text = f"p-{text}"
    return text


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ identity
def sanitize_remote(url: str) -> str | None:
    """Normalise a git remote to host/path, dropping any credentials (user:token@...)."""
    url = url.strip()
    if not url:
        return None
    m = re.match(r"^(?:[\w.\-]+@)?([\w.\-]+):(?!//)(.+)$", url)            # scp-like git@host:owner/repo.git
    if m:
        host, path = m.group(1), m.group(2)
    else:
        m = re.match(r"^[a-z][a-z0-9+.\-]*://(?:[^@/]*@)?([^/:]+)(?::\d+)?/(.+)$", url, re.IGNORECASE)
        if not m:
            return None
        host, path = m.group(1), m.group(2)
    path = re.sub(r"\.git$", "", path.strip("/"))
    return f"{host.lower()}/{path}" if path else None


def _git_remote(root: Path) -> str | None:
    cfg = root / ".git" / "config"
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text, re.DOTALL)
    return sanitize_remote(m.group(1)) if m else None


def project_identity(root: Path) -> tuple[str, str | None]:
    """(stable identity string, sanitised remote). Prefers the git remote so clones/moves stay one project."""
    remote = _git_remote(root)
    return (f"git:{remote}" if remote else f"path:{root.resolve().as_posix()}"), remote


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def root_hash(root: Path) -> str:
    """Identifies a checkout location without storing its path in metadata."""
    return short_hash(f"path:{root.resolve().as_posix()}")


# ------------------------------------------------------------------ workspace
@dataclass
class Workspace:
    path: Path
    slug: str
    name: str
    identity_hash: str
    remote: str | None

    @property
    def runs_dir(self) -> Path:
        return self.path / "runs"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def find_workspace(reports_root: Path, project_root: Path) -> Workspace | None:
    ident, _remote = project_identity(project_root)
    h = short_hash(ident)
    if not reports_root.is_dir():
        return None
    for child in sorted(reports_root.iterdir()):
        meta = _read_json(child / "project.json") if child.is_dir() and not child.name.startswith(".") else None
        if meta and meta.get("identity_hash") == h:
            return Workspace(child, child.name, str(meta.get("name") or child.name), h, meta.get("remote"))
    return None


def find_or_create_workspace(reports_root: Path, project_root: Path) -> Workspace:
    existing = find_workspace(reports_root, project_root)
    if existing:
        return existing
    project_root = project_root.resolve()
    ident, remote = project_identity(project_root)
    h = short_hash(ident)
    slug = slugify(project_root.name)
    folder = reports_root / slug
    if folder.exists():                      # same folder name, different project => distinct, stable folder
        slug = f"{slug}-{h[:6]}"
        folder = reports_root / slug
    folder.mkdir(parents=True, exist_ok=True)
    ws = Workspace(folder, slug, project_root.name, h, remote)
    atomic_write_text(folder / "project.json", json.dumps(
        {"schema_version": SCHEMA_VERSION, "name": ws.name, "slug": slug, "identity_hash": h, "remote": remote,
         "runs": []}, indent=2) + "\n")
    return ws


def new_run_dir(ws: Workspace) -> tuple[str, Path]:
    """Create a unique run folder: <UTC date>_<time>_<6 random hex>. Never reuses an existing one."""
    for _ in range(20):
        run_id = f"{datetime.now(UTC):%Y-%m-%d_%H%M%S}_{secrets.token_hex(3)}"
        path = ws.runs_dir / run_id
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        (path / "assets").mkdir()
        return run_id, path
    raise RuntimeError("could not allocate a unique run directory")


def list_runs(ws: Workspace) -> list[dict]:
    """All runs on disk (oldest first), read from each run's metadata.json."""
    runs = []
    if ws.runs_dir.is_dir():
        for d in ws.runs_dir.iterdir():
            meta = _read_json(d / "metadata.json") if d.is_dir() and _RUN_ID.match(d.name) else None
            if meta:
                runs.append(meta)
    return sorted(runs, key=lambda m: (m.get("created_at", ""), m.get("run_id", "")))


def latest_run(ws: Workspace, project_root: Path | None = None, mode: str | None = None,
               include_provisional: bool = False) -> dict | None:
    """Newest completed run (provisional runs, written while model answers were pending, are skipped)."""
    rh = root_hash(project_root) if project_root else None
    for meta in reversed(list_runs(ws)):
        if meta.get("provisional") and not include_provisional:
            continue
        if rh and meta.get("root_hash") not in (None, rh):
            continue
        if mode and meta.get("mode") != mode:
            continue
        if (ws.runs_dir / meta["run_id"] / "findings.json").is_file():
            return meta
    return None


def run_path(ws: Workspace, run_id: str) -> Path:
    if not _RUN_ID.match(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    return ws.runs_dir / run_id


def update_project_files(ws: Workspace, dashboard_html: str) -> list[dict]:
    """Rewrite project.json (run list rebuilt from disk), index.html and README.md."""
    runs = list_runs(ws)
    atomic_write_text(ws.path / "project.json", json.dumps({
        "schema_version": SCHEMA_VERSION, "name": ws.name, "slug": ws.slug, "identity_hash": ws.identity_hash,
        "remote": ws.remote, "run_count": len(runs), "last_run": runs[-1]["run_id"] if runs else None,
        "runs": [r["run_id"] for r in runs]}, indent=2) + "\n")
    atomic_write_text(ws.path / "index.html", dashboard_html)
    atomic_write_text(ws.path / "README.md", (
        f"# Repo Doctor reports: {ws.name}\n\n"
        "- `index.html`: history of all analyses of this project (open it in a browser)\n"
        "- `project.json`: project identity and run list\n"
        "- `runs/<run-id>/`: one folder per analysis, never overwritten:\n"
        "  `report.html` and `report.md` (same content), `findings.json` (the data both are built from),\n"
        "  `metadata.json` (run summary) and `assets/` (charts)\n"))
    return runs
