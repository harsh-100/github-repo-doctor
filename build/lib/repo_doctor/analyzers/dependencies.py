"""Parse dependency manifests (read-only, no execution)."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from repo_doctor.core.models import Dependency

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$")


def _split_req(line: str) -> Dependency | None:
    line = line.split("#", 1)[0].split(";", 1)[0].strip()
    if not line or line.startswith("-") or "://" in line:
        return None
    m = _REQ_LINE.match(line)
    return Dependency(name=m[1], spec=m[3].strip(), ecosystem="python") if m else None


def parse_requirements_txt(path: Path) -> list[Dependency]:
    deps = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        d = _split_req(line)
        if d:
            deps.append(d)
    return deps


def parse_pyproject(path: Path) -> list[Dependency]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps: list[Dependency] = []
    project = data.get("project", {})
    for line in project.get("dependencies", []):
        d = _split_req(line)
        if d:
            deps.append(d)
    for group in project.get("optional-dependencies", {}).values():
        for line in group:
            d = _split_req(line)
            if d:
                d.dev = True
                deps.append(d)
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in poetry.get("dependencies", {}).items():
        if name.lower() != "python":
            deps.append(Dependency(name=name, spec=spec if isinstance(spec, str) else "", ecosystem="python"))
    return deps


def parse_package_json(path: Path) -> tuple[list[Dependency], dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    deps = []
    for key, dev in (("dependencies", False), ("devDependencies", True)):
        for name, spec in (data.get(key) or {}).items():
            deps.append(Dependency(name=name, spec=str(spec), dev=dev, ecosystem="npm"))
    return deps, data


def parse_manifest(path: Path) -> tuple[list[Dependency], dict]:
    """Return (dependencies, extra). Raises ValueError on malformed files."""
    name = path.name.lower()
    try:
        if name == "requirements.txt":
            return parse_requirements_txt(path), {}
        if name == "pyproject.toml":
            return parse_pyproject(path), {}
        if name == "package.json":
            return parse_package_json(path)
    except (OSError, ValueError, tomllib.TOMLDecodeError, AttributeError) as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    return [], {}
