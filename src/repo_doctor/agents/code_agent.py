"""Code quality: deterministic Python AST checks + LLM review of selected files."""
from __future__ import annotations

import ast

from repo_doctor.core.models import Finding, Severity, Verification
from repo_doctor.providers import ProviderError
from repo_doctor.security.redaction import redact

from .base import JSON_FORMAT, Agent, AgentContext, ask_findings, build_batches

LONG_FUNCTION_LINES = 80
MAX_DETERMINISTIC_PER_FILE = 10

SYSTEM = (
    "You are a meticulous senior code reviewer. Find real code-quality problems: probable bugs, "
    "poor error handling, missing input validation, duplicated logic, needless complexity, "
    "performance problems, unused code. Skip pure style preferences. Use category 'quality/<subtype>'."
)


def _finding(path: str, line: int, severity: Severity, title: str, desc: str, evidence: str, fix: str) -> Finding:
    return Finding(id="tmp", agent="code", category="quality/" + title.split(":")[0].lower().replace(" ", "-"),
                   severity=severity, title=title, description=desc, file_path=path, line=line,
                   evidence=redact(evidence.strip())[:300], suggested_fix=fix, confidence=0.9,
                   verification=Verification.VERIFIED)


def python_checks(path: str, lines: list[str]) -> list[Finding]:
    src = "\n".join(lines)
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        ln = exc.lineno or 1
        text = lines[ln - 1] if 0 < ln <= len(lines) else ""
        return [_finding(path, ln, Severity.HIGH, "Syntax error", f"Python cannot parse this file: {exc.msg}.",
                         text, "Fix the syntax error.")]
    except (ValueError, RecursionError):
        return []
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            out.append(_finding(path, node.lineno, Severity.LOW, "Bare except",
                                "A bare `except:` also catches KeyboardInterrupt/SystemExit and hides bugs.",
                                lines[node.lineno - 1], "Catch specific exceptions (or `except Exception`) and handle or log them."))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = node.args.defaults + [d for d in node.args.kw_defaults if d is not None]
            if any(isinstance(d, (ast.List, ast.Dict, ast.Set)) for d in defaults):
                out.append(_finding(path, node.lineno, Severity.MEDIUM, "Mutable default argument",
                                    f"`{node.name}` uses a mutable default that is shared between calls.",
                                    lines[node.lineno - 1], "Default to None and create the object inside the function."))
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > LONG_FUNCTION_LINES:
                out.append(_finding(path, node.lineno, Severity.LOW, "Large function",
                                    f"`{node.name}` is {length} lines long (threshold {LONG_FUNCTION_LINES}).",
                                    lines[node.lineno - 1], "Split into smaller, focused functions."))
    return sorted(out, key=lambda f: f.line or 0)[:MAX_DETERMINISTIC_PER_FILE]


class CodeAgent(Agent):
    name = "code"
    commands = frozenset({"analyze", "quality"})

    def run(self, ctx: AgentContext) -> list[Finding]:
        inv = ctx.profile.inventory
        source = [f for f in inv.files if f.kind == "source" and f.language]
        findings: list[Finding] = []
        for f in source:
            if f.language == "Python":
                findings += python_checks(f.path, ctx.reader.raw_lines(f.path))
        if not ctx.llm_enabled:
            return findings

        entry = {e for e in ctx.profile.entry_points}
        ordered = [f.path for f in sorted(source, key=lambda f: (f.path not in entry, -f.size))]
        if not ordered:
            return findings
        known = "\n".join(f"- {x.file_path}:{x.line} {x.title}" for x in findings[:40]) or "(none)"
        batches, covered = build_batches(ctx, ordered)
        if covered < len(ordered):
            ctx.notes.append(f"code: LLM reviewed {covered} of {len(ordered)} source files (call limit); "
                             "larger and entry-point files were prioritised.")
        for body, sent in batches:
            prompt = (f"Already detected by static checks (do not repeat):\n{known}\n\n"
                      f"Review this code:\n\n{body}\n\n{JSON_FORMAT}")
            try:
                findings += ask_findings(ctx, self.name, "quality", SYSTEM, prompt, sent)
            except ProviderError as exc:
                ctx.warnings.append(f"code: LLM batch failed: {exc}")
        return findings
