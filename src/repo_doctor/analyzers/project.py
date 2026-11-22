"""Build a ProjectProfile from an inventory + manifests. Deterministic; no LLM."""
from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath

from repo_doctor.core.models import Inventory, ProjectProfile
from repo_doctor.reports.language import fixture_marker

from .dependencies import parse_manifest

JS_FRAMEWORKS = {
    "react": "React", "next": "Next.js", "vue": "Vue", "svelte": "Svelte", "express": "Express",
    "fastify": "Fastify", "@nestjs/core": "NestJS", "vite": "Vite", "@angular/core": "Angular",
}
PY_FRAMEWORKS = {
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI", "typer": "Typer", "click": "Click",
    "pydantic": "Pydantic", "sqlalchemy": "SQLAlchemy", "streamlit": "Streamlit",
}
TEST_TOOLS = {
    "pytest": "pytest", "jest": "Jest", "vitest": "Vitest", "mocha": "Mocha",
    "@playwright/test": "Playwright", "cypress": "Cypress", "nose2": "nose2",
}
JS_ENTRY_CANDIDATES = ("index.js", "index.ts", "src/index.js", "src/index.ts", "src/main.ts",
                       "src/main.js", "server.js", "app.js", "src/App.tsx", "src/app.ts")
PY_ENTRY_NAMES = {"main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "cli.py", "__main__.py"}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def build_profile(root: Path, inv: Inventory) -> ProjectProfile:
    paths = {f.path for f in inv.files}
    langs = Counter(f.language for f in inv.files if f.language)
    profile = ProjectProfile(name=root.name, root=str(root), languages=dict(langs.most_common()),
                             inventory=inv)
    concerns = profile.concerns

    # --- manifests & dependencies ---
    scripts: dict[str, str] = {}
    for f in inv.files:
        if f.kind != "manifest":
            continue
        profile.manifests.append(f.path)
        try:
            deps, extra = parse_manifest(root / f.path)
        except ValueError as exc:
            concerns.append(f"Could not parse manifest ({exc})")
            continue
        for d in deps:
            d.source = f.path
        profile.dependencies.extend(deps)
        if f.path.endswith("package.json"):
            scripts.update(extra.get("scripts") or {})
            for key in ("main", "module"):
                if isinstance(extra.get(key), str):
                    profile.entry_points.append(str(PurePosixPath(f.path).parent / extra[key]).lstrip("./"))
        if f.path.endswith("pyproject.toml"):
            try:
                import tomllib
                data = tomllib.loads((root / f.path).read_text(encoding="utf-8"))
                for script, target in (data.get("project", {}).get("scripts") or {}).items():
                    profile.entry_points.append(f"{script} -> {target} ({f.path})")
            except (OSError, ValueError):
                pass

    # dependencies declared inside examples/tests/fixtures describe those fixtures, not the project itself
    dep_names = {_norm(d.name) for d in profile.dependencies if not fixture_marker(d.source)}
    profile.frameworks = sorted(
        {v for k, v in JS_FRAMEWORKS.items() if _norm(k) in dep_names}
        | {v for k, v in PY_FRAMEWORKS.items() if _norm(k) in dep_names}
    )
    tests = {v for k, v in TEST_TOOLS.items() if _norm(k) in dep_names}
    if any(p.endswith(("pytest.ini", "conftest.py")) for p in paths):
        tests.add("pytest")
    if any(f.kind == "test" and f.language == "Python" for f in inv.files) and "pytest" not in tests:
        tests.add("unittest/pytest (inferred from test files)")
    if "test" in scripts:
        profile.build_files.append(f"npm script test: {scripts['test']}")
    profile.test_frameworks = sorted(tests)

    managers = []
    if any(p.endswith("package.json") for p in paths):
        managers.append("npm")
    if any(p.endswith(("pyproject.toml", "requirements.txt")) for p in paths):
        managers.append("pip")
    profile.package_managers = managers

    # --- entry points, docs, build files, structure ---
    for cand in JS_ENTRY_CANDIDATES:
        if cand in paths and cand not in profile.entry_points:
            profile.entry_points.append(cand)
    for f in inv.files:
        name = PurePosixPath(f.path).name
        if f.language == "Python" and name in PY_ENTRY_NAMES and f.kind != "test":
            profile.entry_points.append(f.path)
        if f.kind == "docs" and PurePosixPath(f.path).parent.as_posix() in (".", "docs"):
            profile.docs.append(f.path)
        if f.kind == "build":
            profile.build_files.append(f.path)
    top = Counter(PurePosixPath(f.path).parts[0] for f in inv.files if len(PurePosixPath(f.path).parts) > 1)
    profile.top_level_modules = dict(top.most_common())

    # --- concerns (facts only) ---
    if not inv.files:
        concerns.append("No analysable files found.")
    if not any(f.kind == "test" for f in inv.files):
        concerns.append("No test files detected (check for non-conventional test locations).")
    if not any(d.lower().startswith("readme") for d in (PurePosixPath(p).name for p in paths)):
        concerns.append("No README found.")
    if not langs:
        concerns.append("No Python/JavaScript/TypeScript source files found (unsupported project type?).")
    if inv.truncated:
        concerns.append("Scan hit the file limit; the inventory is incomplete.")
    return profile
