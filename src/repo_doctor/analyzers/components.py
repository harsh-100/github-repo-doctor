"""Find what a repository actually defines: CLI commands, Claude Code commands/agents/skills, npm scripts,
HTTP routes and CI workflows. Reads files only (never runs them); every component records where it was found."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from repo_doctor.core.models import ProjectProfile
from repo_doctor.reports.language import fixture_marker
from repo_doctor.tools.file_reader import RepoReader

MAX_FILES = 300
_ROUTE_PY = re.compile(r"""@\w+\.(?:route|get|post|put|delete|patch)\(\s*["']([^"']+)["']""")
_ROUTE_JS = re.compile(r"""\b(?:app|router)\.(get|post|put|delete|patch)\(\s*["'`]([^"'`]+)["'`]""")
_FRONT_DESC = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


@dataclass
class Component:
    kind: str  # cli-command | claude-command | claude-agent | claude-skill | npm-script | http-route | ci-workflow
    name: str
    file: str
    line: int | None = None
    description: str = ""


def _first_line(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().split("\n")[0])[:160]


def _md_description(text: str) -> str:
    """Frontmatter `description:`, else the first heading ("# /rank - Triage jobs" -> "Triage jobs"), else first sentence."""
    m = _FRONT_DESC.search(text[:800])
    if m:
        return _first_line(m.group(1))
    body = re.sub(r"\A---.*?---\s*", "", text, flags=re.DOTALL)
    for line in body.splitlines():
        t = line.strip()
        if not t:
            continue
        if t.startswith("#"):
            t = t.lstrip("# ").strip()
            t = re.sub(r"^/?[\w-]+\s*(?:[-:\u2013\u2014]+)\s*", "", t)  # drop a leading "/name -" or "name:"
            return _first_line(t)
        return _first_line(re.split(r"(?<=[.!?])\s", t)[0])
    return ""


def _python_commands(path: str, source: str) -> list[Component]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            target = call.func if call else dec
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name in {"command", "group"}:  # Typer / Click
                cmd = node.name.replace("_", "-")
                if call and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    cmd = call.args[0].value
                elif call:
                    for kw in call.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            cmd = str(kw.value.value)
                found.append(Component("cli-command", cmd, path, node.lineno, _first_line(ast.get_docstring(node))))
    return found


def find_components(profile: ProjectProfile, reader: RepoReader) -> list[Component]:
    out: list[Component] = []
    files = [f for f in profile.inventory.files]
    for f in files[:3000]:
        p = PurePosixPath(f.path)
        parts = p.parts
        if len(parts) >= 3 and parts[-3:-1] == ("claude", "commands") or (len(parts) >= 3 and parts[0] == ".claude" and parts[1] == "commands"):
            out.append(Component("claude-command", "/" + p.stem, f.path, None, _md_description(reader.text(f.path))))
        elif len(parts) >= 3 and parts[0] == ".claude" and parts[1] == "agents" and p.suffix == ".md":
            out.append(Component("claude-agent", p.stem, f.path, None, _md_description(reader.text(f.path))))
        elif len(parts) >= 4 and parts[0] == ".claude" and parts[1] == "skills" and p.name == "SKILL.md":
            out.append(Component("claude-skill", parts[2], f.path, None, _md_description(reader.text(f.path))))
        elif f.path.startswith(".github/workflows/") and p.suffix in {".yml", ".yaml"}:
            m = re.search(r"^name:\s*(.+)$", reader.text(f.path)[:600], re.MULTILINE)
            out.append(Component("ci-workflow", _first_line(m.group(1)) if m else p.stem, f.path))

    src = [f for f in files if f.kind == "source" and f.language and not fixture_marker(f.path)][:MAX_FILES]
    for f in src:
        text = reader.text(f.path)
        if f.language == "Python":
            out += _python_commands(f.path, "\n".join(reader.raw_lines(f.path)))
            routes = _ROUTE_PY.findall(text)
        else:
            routes = [f"{m} {r}" for m, r in _ROUTE_JS.findall(text)]
        for r in routes[:10]:
            out.append(Component("http-route", r, f.path))

    for m in profile.manifests:
        if m.endswith("package.json"):
            try:
                data = json.loads(reader.text(m))
            except ValueError:
                continue
            for name, cmd in (data.get("scripts") or {}).items():
                out.append(Component("npm-script", name, m, None, _first_line(str(cmd))))
            bins = data.get("bin")
            for name in ([bins] if isinstance(bins, str) else list(bins or {})):
                out.append(Component("cli-command", str(name), m))
    return out[:200]
