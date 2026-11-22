"""Lightweight sequential orchestrator. Add an agent by registering it; no changes needed here."""
from __future__ import annotations

from collections.abc import Callable

from repo_doctor.agents.base import Agent, AgentContext
from repo_doctor.agents.code_agent import CodeAgent
from repo_doctor.agents.reviewer_agent import ReviewerAgent
from repo_doctor.agents.security_agent import SecurityAgent
from repo_doctor.agents.testing_agent import TestingAgent
from repo_doctor.analyzers.static_analysis import ToolSuite
from repo_doctor.core.dedup import deduplicate
from repo_doctor.core.explainer import explain_tool_findings
from repo_doctor.core.models import AnalysisResult, Finding, Severity

MODES = ("analyze", "security", "quality", "tests")
_SEV_ORDER = {s: i for i, s in enumerate(Severity)}

Progress = Callable[[str, str], None]  # (step name, "running" | "done" | "failed")


def default_agents() -> list[Agent]:
    return [CodeAgent(), SecurityAgent(), TestingAgent()]


class Orchestrator:
    def __init__(self, ctx: AgentContext, agents: list[Agent] | None = None,
                 reviewer: ReviewerAgent | None = None, tools: ToolSuite | None = None):
        self.ctx = ctx
        self.tools = tools
        self.agents = agents if agents is not None else default_agents()
        self.reviewer = reviewer or ReviewerAgent()

    def steps(self, mode: str) -> list[Agent]:
        return [a for a in self.agents if mode in a.commands]

    def has_tool_step(self, mode: str) -> bool:
        return bool(self.tools and self.tools.selected(mode))

    def total_steps(self, mode: str) -> int:
        return len(self.steps(mode)) + 1 + int(self.has_tool_step(mode))

    def run(self, mode: str, progress: Progress | None = None) -> AnalysisResult:
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from {', '.join(MODES)}")
        note = progress or (lambda *_: None)
        result = AnalysisResult(mode=mode, llm_used=self.ctx.llm_enabled)
        raw: list[Finding] = []

        tool_notes: list[str] = []
        if self.has_tool_step(mode):
            note("static", "running")
            try:
                found, result.tools, tool_notes = self.tools.run(  # type: ignore[union-attr]
                    mode, self.ctx.root, self.ctx.profile, self.ctx.reader)
                raw += found
                note("static", "done")
            except Exception as exc:  # noqa: BLE001 - reported in result
                result.agent_errors["static"] = f"{type(exc).__name__}: {exc}"
                note("static", "failed")

        for agent in self.steps(mode):
            note(agent.name, "running")
            try:
                raw += agent.run(self.ctx)
                result.agents_run.append(agent.name)
                note(agent.name, "done")
            except Exception as exc:  # noqa: BLE001 - isolate agent failures; reported in result
                result.agent_errors[agent.name] = f"{type(exc).__name__}: {exc}"
                note(agent.name, "failed")

        note("reviewer", "running")
        deduped, merged = deduplicate(raw)
        result.duplicates_merged = merged
        for i, f in enumerate(deduped):
            f.id = f"tmp-{i}"  # stable temp ids for the reviewer
        try:
            kept, rejected = self.reviewer.review(self.ctx, deduped)
            note("reviewer", "done")
        except Exception as exc:  # noqa: BLE001 - reported in result
            result.agent_errors["reviewer"] = f"{type(exc).__name__}: {exc}"
            kept, rejected = deduped, []
            note("reviewer", "failed")

        kept = explain_tool_findings(self.ctx, kept)
        kept.sort(key=lambda f: (_SEV_ORDER[f.severity], -f.confidence, f.file_path or "", f.line or 0))
        for i, f in enumerate(kept, 1):
            f.id = f"F-{i:03d}"
        for i, f in enumerate(rejected, 1):
            f.id = f"R-{i:03d}"
        result.findings, result.rejected = kept, rejected
        result.llm_calls = self.ctx.llm_calls
        result.notes = list(dict.fromkeys(self.ctx.notes + tool_notes + [f"WARNING: {w}" for w in self.ctx.warnings]))
        return result
