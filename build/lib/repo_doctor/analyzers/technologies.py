"""Technologies, checked against actual usage: a library only counts as *used* when source code imports it.
Declared-but-never-imported packages and packages used only in tests/examples are labelled as such."""
from __future__ import annotations

import ast
import re

from repo_doctor.core.models import ProjectProfile, Tech
from repo_doctor.reports.language import fixture_marker
from repo_doctor.tools.file_reader import RepoReader

# package (normalised) -> (display name, kind)
KNOWN: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic API", "ai"), "@anthropic-ai/sdk": ("Anthropic API", "ai"), "openai": ("OpenAI API", "ai"),
    "langchain": ("LangChain", "ai"), "langchain-core": ("LangChain", "ai"), "llama-index": ("LlamaIndex", "ai"),
    "google-generativeai": ("Google Gemini", "ai"), "ollama": ("Ollama", "ai"), "transformers": ("Hugging Face Transformers", "ai"),
    "torch": ("PyTorch", "ai"), "tensorflow": ("TensorFlow", "ai"), "ai": ("Vercel AI SDK", "ai"),
    "sqlalchemy": ("SQLAlchemy", "database"), "psycopg2": ("PostgreSQL (psycopg2)", "database"),
    "psycopg": ("PostgreSQL (psycopg)", "database"), "pymongo": ("MongoDB", "database"), "redis": ("Redis", "database"),
    "mongoose": ("MongoDB (mongoose)", "database"), "pg": ("PostgreSQL (pg)", "database"), "mysql2": ("MySQL", "database"),
    "sqlite3": ("SQLite", "database"), "prisma": ("Prisma", "database"), "@prisma/client": ("Prisma", "database"),
    "sequelize": ("Sequelize", "database"), "typeorm": ("TypeORM", "database"),
    "boto3": ("AWS (boto3)", "service"), "stripe": ("Stripe", "service"), "twilio": ("Twilio", "service"),
    "firebase-admin": ("Firebase", "service"), "supabase": ("Supabase", "service"), "@supabase/supabase-js": ("Supabase", "service"),
    "sendgrid": ("SendGrid", "service"), "slack-sdk": ("Slack", "service"), "@slack/web-api": ("Slack", "service"),
    "react": ("React", "framework"), "next": ("Next.js", "framework"), "vue": ("Vue", "framework"),
    "flask": ("Flask", "framework"), "django": ("Django", "framework"), "fastapi": ("FastAPI", "framework"),
    "express": ("Express", "framework"), "typer": ("Typer", "framework"), "click": ("Click", "framework"),
    "streamlit": ("Streamlit", "framework"), "@nestjs/core": ("NestJS", "framework"), "svelte": ("Svelte", "framework"),
}
_PY_IMPORT_NAME = {"pyyaml": "yaml", "beautifulsoup4": "bs4", "pillow": "PIL", "scikit-learn": "sklearn",
                   "python-dotenv": "dotenv", "opencv-python": "cv2", "google-generativeai": "google.generativeai",
                   "psycopg2-binary": "psycopg2", "python-dateutil": "dateutil", "attrs": "attr"}
_JS_IMPORT = re.compile(r"""(?:from\s+|require\(\s*|import\s*\(\s*|import\s+)["']([^"'./][^"']*)["']""")
_ENV = re.compile(r"\b(ANTHROPIC|OPENAI|GEMINI|GOOGLE_API|MISTRAL|COHERE|HUGGINGFACE|GROQ)\w*_?(?:API_)?KEY\b")
_SKIP = {"pytest", "ruff", "eslint", "jest", "vitest", "mypy", "black", "flake8", "typescript", "prettier", "setuptools",
         "wheel", "pip", "mocha", "nodemon", "ts-node", "webpack", "vite", "babel", "types"}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def _py_imports(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module)
    return mods


def _js_pkg(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def detect_technologies(profile: ProjectProfile, reader: RepoReader) -> list[Tech]:
    inv = profile.inventory
    used_src: set[str] = set()
    used_test: set[str] = set()
    for f in inv.files:
        if not f.language or len(inv.files) > 4000:
            continue
        in_tests = f.kind == "test" or bool(fixture_marker(f.path))
        mods: set[str] = set()
        if f.language == "Python":
            mods = {m.split(".")[0] if not m.startswith("google.") else m for m in _py_imports("\n".join(reader.raw_lines(f.path)))}
        else:
            mods = {_js_pkg(m) for m in _JS_IMPORT.findall(reader.text(f.path))}
        (used_test if in_tests else used_src).update(mods)

    def usage(dep_name: str, ecosystem: str) -> str:
        n = _norm(dep_name)
        cands = {n, n.replace("-", "_")}
        if ecosystem == "python":
            alias = _PY_IMPORT_NAME.get(n)
            if alias:
                cands |= {alias, alias.split(".")[0]}
        cands |= {c.lower() for c in cands}
        norm = lambda s: {_norm(x) for x in s}
        if cands & used_src or norm(cands) & norm(used_src):
            return "confirmed"
        if cands & used_test or norm(cands) & norm(used_test):
            return "tests-only"
        return "declared-only"

    techs: list[Tech] = []
    for lang, n in profile.languages.items():
        own = len([f for f in inv.files if f.language == lang and f.kind != "test" and not fixture_marker(f.path)])
        techs.append(Tech(name=lang, kind="language", confidence="confirmed" if own else "tests-only",
                          evidence=f"{own} project source file(s)" if own else f"{n} file(s), all in tests or examples"))
    seen: set[str] = set()
    for d in profile.dependencies:
        if fixture_marker(d.source):
            continue  # declared by an example/test fixture, not by the project
        key = _norm(d.name)
        if key in _SKIP or key.startswith("@types/") or d.dev and key not in KNOWN:
            continue
        display, kind = KNOWN.get(key, (d.name, "library"))
        if display in seen:
            continue
        seen.add(display)
        conf = usage(d.name, d.ecosystem)
        if d.dev and conf == "declared-only":
            continue  # dev-only declarations are noise
        ev = {"confirmed": "imported in source code", "tests-only": "imported only in tests/examples",
              "declared-only": "listed in a dependency file but never imported"}[conf]
        techs.append(Tech(name=display, kind=kind, confidence=conf, evidence=ev))
    # technologies that show up as files rather than imports
    paths = [f.path for f in inv.files if not fixture_marker(f.path)]
    for label, kind, test, what in (
            ("Claude Code", "ai", lambda p: p.startswith(".claude/") and p.endswith(".md"), "slash command/agent/skill files in .claude/"),
            ("LaTeX", "tool", lambda p: p.endswith(".tex"), ".tex file(s)"),
            ("Docker", "tool", lambda p: p.split("/")[-1].lower() in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}, "Docker file(s)"),
            ("GitHub Actions", "tool", lambda p: p.startswith(".github/workflows/"), "workflow file(s)")):
        n = len([p for p in paths if test(p)])
        if n and label not in seen:
            techs.append(Tech(name=label, kind=kind, confidence="confirmed", evidence=f"{n} {what}"))
    # stdlib sqlite and AI keys mentioned in config/docs
    if "sqlite3" in used_src and "SQLite" not in seen:
        techs.append(Tech(name="SQLite", kind="database", confidence="confirmed", evidence="sqlite3 imported in source code"))
    env_text = "\n".join(reader.text(f.path)[:4000] for f in inv.files if f.path.endswith((".env.example", ".env.sample", "README.md")))
    if _ENV.search(env_text) and not any(t.kind == "ai" for t in techs):
        techs.append(Tech(name="AI provider API key", kind="ai", confidence="mentioned",
                          evidence="an API key variable is mentioned in the docs or .env.example"))
    order = {"language": 0, "framework": 1, "ai": 2, "database": 3, "service": 4, "library": 5, "tool": 6}
    rank = {"confirmed": 0, "tests-only": 1, "mentioned": 2, "declared-only": 3}
    return sorted(techs, key=lambda t: (order.get(t.kind, 9), rank[t.confidence], t.name.lower()))
