from __future__ import annotations

from pathlib import PurePosixPath

EXTENSIONS: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
}
CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}
DOC_SUFFIXES = {".md", ".rst", ".txt"}
BUILD_NAMES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "makefile", "procfile",
    "vercel.json", "netlify.toml", "tox.ini", "noxfile.py",
}
MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "pipfile", "tsconfig.json",
}
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
    "pipfile.lock", "bun.lockb", "composer.lock", "cargo.lock",
}


def detect_language(path: str) -> str | None:
    return EXTENSIONS.get(PurePosixPath(path).suffix.lower())


def classify(path: str) -> str:
    p = PurePosixPath(path)
    name, suffix = p.name.lower(), p.suffix.lower()
    parts = {x.lower() for x in p.parts[:-1]}
    if name in MANIFEST_NAMES:
        return "manifest"
    if name in BUILD_NAMES or "workflows" in parts and suffix in {".yml", ".yaml"}:
        return "build"
    is_test_name = (
        name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".test.ts",
        ".test.tsx", ".test.jsx", ".spec.js", ".spec.ts", ".spec.tsx", ".spec.jsx"))
        or name in {"conftest.py"}
    )
    if is_test_name or parts & {"tests", "test", "__tests__", "spec"}:
        return "test"
    if suffix in EXTENSIONS:
        return "source"
    if suffix in DOC_SUFFIXES or "docs" in parts:
        return "docs"
    if suffix in CONFIG_SUFFIXES or name.startswith("."):
        return "config"
    return "other"
