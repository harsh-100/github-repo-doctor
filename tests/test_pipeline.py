import json

import pytest
from fakes import FakeProvider

from repo_doctor.agents.base import Agent
from repo_doctor.agents.reviewer_agent import ReviewerAgent, check_evidence
from repo_doctor.core.dedup import deduplicate
from repo_doctor.core.models import AnalysisResult, Finding, Severity, Verification
from repo_doctor.core.orchestrator import Orchestrator, default_agents
from repo_doctor.providers import ProviderError
from repo_doctor.reports.health import render_health


def F(**kw):
    base = {"id": "x", "agent": "code", "category": "quality/a", "severity": Severity.MEDIUM,
            "title": "Bare except", "description": "d", "file_path": "a.py", "line": 5,
            "evidence": "except:", "suggested_fix": "fix", "confidence": 0.5,
            "verification": Verification.SUPPORTED}
    return Finding(**{**base, **kw})


# ---- dedup
def test_dedup_merges_same_issue_across_agents_keeps_best_evidence():
    a = F(agent="code", verification=Verification.UNVERIFIED, confidence=0.3)
    b = F(agent="security", title="Bare except clause", verification=Verification.VERIFIED, confidence=0.9, line=6)
    out, merged = deduplicate([a, b])
    assert merged == 1 and len(out) == 1
    assert out[0].verification == Verification.VERIFIED and out[0].agent == "code, security"


def test_dedup_keeps_distinct_issues_on_adjacent_lines():
    a = F(title="API key in source", category="security/hardcoded-secret", line=5)
    b = F(title="Credentials embedded in URL", category="security/hardcoded-secret", line=6)
    out, merged = deduplicate([a, b])
    assert merged == 0 and len(out) == 2


def test_dedup_same_line_same_category_merges_and_different_files_do_not():
    assert deduplicate([F(title="aaa"), F(title="zzz")])[1] == 1
    assert deduplicate([F(), F(file_path="b.py")])[1] == 0


# ---- reviewer
@pytest.fixture
def ctx(tmp_path, make_ctx):
    (tmp_path / "a.py").write_text("x = 1\n" * 10)
    return make_ctx(tmp_path)


def test_check_evidence_rejects_missing_file_and_drops_bad_line(ctx):
    assert check_evidence(ctx, F(file_path="nope.py")).verification == Verification.REJECTED
    fixed = check_evidence(ctx, F(line=500))
    assert fixed.line is None and fixed.confidence <= 0.3
    assert check_evidence(ctx, F(file_path=None)).file_path is None


def test_check_evidence_trusts_verified_deterministic_findings(ctx):
    f = F(file_path=".env", verification=Verification.VERIFIED)
    assert check_evidence(ctx, f).verification == Verification.VERIFIED


def test_reviewer_applies_verdicts_and_never_adds_findings(ctx):
    fs = [F(id="A"), F(id="B"), F(id="C"), F(id="D", verification=Verification.VERIFIED)]
    ctx.provider = FakeProvider({"reviews": [
        {"id": "A", "verdict": "confirm", "reason": "ok"},
        {"id": "B", "verdict": "reject", "reason": "wrong"},
        {"id": "C", "verdict": "speculative", "fix_ok": False, "reason": "maybe"},
        {"id": "NEW", "verdict": "confirm", "reason": "invented id"},
    ]})
    kept, rejected = ReviewerAgent().review(ctx, fs)
    by = {f.id: f for f in kept}
    assert set(by) == {"A", "C", "D"} and [f.id for f in rejected] == ["B"]
    assert by["C"].verification == Verification.SPECULATIVE and by["C"].suggested_fix == ""
    assert by["C"].confidence == 0.25
    assert by["D"].verification == Verification.VERIFIED  # never sent to / changed by the LLM
    assert len(kept) + len(rejected) == len(fs)


def test_reviewer_survives_llm_failure_and_bad_json(ctx):
    ctx.provider = FakeProvider(ProviderError("down"))
    kept, _ = ReviewerAgent().review(ctx, [F(id="A")])
    assert len(kept) == 1 and any("reviewer" in w for w in ctx.warnings)
    ctx.provider = FakeProvider("not json")
    assert len(ReviewerAgent().review(ctx, [F(id="A")])[0]) == 1


def test_reviewer_prompt_contains_code_excerpt_redacted(tmp_path, make_ctx):
    (tmp_path / "a.py").write_text('password = "hunter2secret"\n')
    c = make_ctx(tmp_path)
    c.provider = FakeProvider({"reviews": []})
    ReviewerAgent().review(c, [F(id="A", line=1, evidence='password = "hunter2secret"')])
    assert "hunter2secret" not in c.provider.prompts[0]


# ---- orchestrator
def test_orchestrator_modes_select_agents(ctx):
    o = Orchestrator(ctx)
    assert [a.name for a in o.steps("security")] == ["security"]
    assert [a.name for a in o.steps("quality")] == ["code"]
    assert [a.name for a in o.steps("tests")] == ["testing"]
    assert [a.name for a in o.steps("analyze")] == ["code", "security", "testing"]
    with pytest.raises(ValueError):
        o.run("bogus")


class Boom(Agent):
    name, commands = "boom", frozenset({"analyze"})

    def run(self, ctx):
        raise RuntimeError("kaboom")


class Custom(Agent):
    name, commands = "custom", frozenset({"analyze"})

    def run(self, ctx):
        return [F(agent="custom", category="quality/custom", title="From custom agent", file_path="a.py", line=1,
                  evidence="x = 1")]


def test_failing_agent_is_isolated_and_new_agents_plug_in(ctx):
    events = []
    r = Orchestrator(ctx, agents=[Boom(), Custom()]).run("analyze", lambda s, st: events.append((s, st)))
    assert "boom" in r.agent_errors and "kaboom" in r.agent_errors["boom"]
    assert r.agents_run == ["custom"] and r.findings[0].title == "From custom agent"
    assert ("boom", "failed") in events and ("reviewer", "done") in events


def test_orchestrator_sorts_assigns_ids_and_separates_rejected(tmp_path, make_ctx):
    (tmp_path / "a.py").write_text("x = 1\n" * 10)
    c = make_ctx(tmp_path)

    class Many(Agent):
        name, commands = "many", frozenset({"analyze"})

        def run(self, ctx):
            return [F(title="low one", severity=Severity.LOW, line=1), F(title="crit one", severity=Severity.CRITICAL, line=9, category="security/x"),
                    F(title="ghost", file_path="ghost.py", line=1)]

    r = Orchestrator(c, agents=[Many()]).run("analyze")
    assert [f.id for f in r.findings] == ["F-001", "F-002"]
    assert r.findings[0].severity == Severity.CRITICAL
    assert [f.title for f in r.rejected] == ["ghost"] and r.rejected[0].id == "R-001"
    assert r.health() == "Critical"


def test_full_pipeline_deterministic_on_sample(sample_repo, make_ctx):
    r = Orchestrator(make_ctx(sample_repo)).run("analyze")
    titles = {f.title for f in r.findings}
    assert "API key or token in source" in titles and any("Sensitive file" in t for t in titles)
    assert not r.llm_used and r.llm_calls == 0
    assert all(f.verification == Verification.VERIFIED for f in r.findings if f.agent != "testing")


def test_full_pipeline_with_mock_llm_counts_calls(sample_repo, make_ctx):
    p = FakeProvider({"findings": [], "reviews": []})
    r = Orchestrator(make_ctx(sample_repo, p)).run("analyze")
    assert r.llm_used and r.llm_calls == len(p.prompts) == 2  # code + security batches


# ---- health & report
def test_health_levels():
    def h(*sev):
        return AnalysisResult(mode="analyze", findings=[F(severity=s, line=i) for i, s in enumerate(sev, 1)]).health()
    assert h() == "Healthy" and h(Severity.LOW) == "Fair"
    assert h(Severity.HIGH) == "Needs Attention" and h(Severity.CRITICAL) == "Critical"
    assert h(*[Severity.MEDIUM] * 6) == "Needs Attention"
    spec = AnalysisResult(mode="analyze", findings=[F(severity=Severity.CRITICAL, verification=Verification.SPECULATIVE)])
    assert spec.health() == "Healthy"  # speculative findings don't drive health


def test_report_has_all_sections_and_no_secrets(sample_repo, make_ctx):
    c = make_ctx(sample_repo)
    r = Orchestrator(c).run("analyze")
    md = render_health(c.profile, r)
    for n in range(1, 12):
        assert f"## {n}. " in md
    assert "Project health: Critical" in md and "Evidence" in md and "Recommended action" in md
    for secret in ("hunter2secret", "sk-ant-abcdefghijklmnop1234567890", "abcdef123456"):
        assert secret not in md and secret not in r.model_dump_json()


def test_report_escapes_code_fences_and_marks_scope(ctx):
    f = F(evidence="a ``` b", verification=Verification.UNVERIFIED)
    r = AnalysisResult(mode="security", findings=[f])
    md = render_health(ctx.profile, r)
    assert "a ``` b" not in md and "Scope: **Security analysis** only" in md
    assert "unverified" in md


def test_findings_json_roundtrip():
    r = AnalysisResult(mode="analyze", findings=[F()])
    assert AnalysisResult.model_validate(json.loads(r.model_dump_json())).findings[0].title == "Bare except"


def test_default_agents_are_the_three_specialists():
    assert [a.name for a in default_agents()] == ["code", "security", "testing"]
