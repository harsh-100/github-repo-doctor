"""LLM explanations for tool findings. Adds `explanation` (and a fix if none) only; the tool's
evidence, severity, location and title are never modified."""
from __future__ import annotations

import json

from repo_doctor.agents.base import AgentContext, extract_json
from repo_doctor.core.models import Finding, Severity
from repo_doctor.providers import ProviderError
from repo_doctor.security.redaction import redact

MAX_EXPLAINED = 15
SYSTEM = (
    "You explain static-analysis findings to developers in plain language. For each finding give a "
    "1-3 sentence explanation of why it matters and how to resolve it. Do not dispute the tool, do not "
    "change severity or location. Reply with ONLY JSON: "
    '{"explanations": [{"id": "...", "explanation": "...", "suggested_fix": "..."}]}'
)
_ORDER = list(Severity)


def _context(ctx: AgentContext, f: Finding, radius: int = 3) -> str:
    if not f.file_path or not f.line or not ctx.reader.normalize(f.file_path):
        return ""
    lines = ctx.reader.lines(f.file_path)  # redacted view
    lo, hi = max(1, f.line - radius), min(len(lines), f.line + radius)
    return "\n".join(f"{i:>5}| {lines[i - 1][:200]}" for i in range(lo, hi + 1))


def explain_tool_findings(ctx: AgentContext, findings: list[Finding]) -> list[Finding]:
    if not ctx.llm_enabled:
        return findings
    tool = sorted((f for f in findings if f.agent.startswith("tool:") and not f.explanation),
                  key=lambda f: _ORDER.index(f.severity))[:MAX_EXPLAINED]
    if not tool:
        return findings
    payload = [{k: redact(v) if isinstance(v, str) else v for k, v in {
        "id": f.id, "tool": f.agent, "rule_and_message": f.title, "severity": f.severity.value,
        "file": f.file_path, "line": f.line, "code": _context(ctx, f)}.items()} for f in tool]
    try:
        ctx.llm_calls += 1
        data = extract_json(ctx.provider.complete(SYSTEM, json.dumps(payload)))  # type: ignore[union-attr]
    except ProviderError as exc:
        ctx.warnings.append(f"explainer: {exc}")
        return findings
    if not data or not isinstance(data.get("explanations"), list):
        ctx.warnings.append("explainer: model returned invalid JSON; explanations skipped.")
        return findings
    by_id = {str(e.get("id")): e for e in data["explanations"] if isinstance(e, dict)}
    out = []
    for f in findings:
        e = by_id.get(f.id) if f.agent.startswith("tool:") else None
        if e:
            upd = {"explanation": redact(str(e.get("explanation", "")))[:600]}
            if not f.suggested_fix and e.get("suggested_fix"):
                upd["suggested_fix"] = redact(str(e["suggested_fix"]))[:600]
            f = f.model_copy(update=upd)
        out.append(f)
    return out
