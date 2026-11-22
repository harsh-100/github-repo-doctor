import json
import sys
from pathlib import Path

import pytest
from fakes import FakeProvider

from repo_doctor.analyzers import static_analysis as sa
from repo_doctor.analyzers.repository import DEFAULT_EXCLUDED_DIRS, load_ignore_spec
from repo_doctor.analyzers.tool_parsers import (
    MAX_FINDINGS_PER_TOOL,
    ParseContext,
    ToolOutputError,
    parse_bandit,
    parse_eslint,
    parse_gitleaks,
    parse_npm_audit,
    parse_ruff,
    parse_tsc,
    ruff_severity,
)
from repo_doctor.core.explainer import explain_tool_findings
from repo_doctor.core.models import Finding, Severity, Verification
from repo_doctor.core.orchestrator import Orchestrator
from repo_doctor.security.permissions import ApprovalPolicy, CommandNotAllowed, validate_command
from repo_doctor.tools.command_runner import run_command, safe_env


@pytest.fixture
def repo(tmp_path, make_ctx):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("import os\n\ndef f(x=[]):\n    try:\n        pass\n    except:\n        pass\n")
    (tmp_path / "web.js").write_text("var x = 1\neval(x)\n")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("import sys\n")
    return tmp_path


@pytest.fixture
def pctx(repo, make_ctx):
    c = make_ctx(repo)
    return ParseContext(repo.resolve(), c.reader, load_ignore_spec(repo.resolve(), []), set(DEFAULT_EXCLUDED_DIRS))


# ---------------------------------------------------------------- permissions & runner
def test_validate_command_allowlist():
    validate_command(["ruff", "check"])
    validate_command(["/x/node_modules/.bin/eslint", "."])
    validate_command(["npm", "audit", "--json"])
    for bad in (["rm", "-rf", "/"], ["bash", "-c", "x"], ["npm", "run", "test"], ["npm"], [], ["python", "x.py"]):
        with pytest.raises(CommandNotAllowed):
            validate_command(bad)


def test_run_command_refuses_non_allowlisted(tmp_path):
    with pytest.raises(CommandNotAllowed):
        run_command(["echo", "hi"], tmp_path, 5)


def test_safe_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    env = safe_env()
    assert not any("KEY" in k or "TOKEN" in k for k in env) and "PATH" in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"  # stale .pyc must never mask a bad fix


def test_run_command_runs_real_tool_without_shell(tmp_path):
    ruff = Path(sys.executable).parent / "ruff"
    if not ruff.exists():
        pytest.skip("ruff not installed")
    (tmp_path / "x.py").write_text("import os\n")
    res = run_command([str(ruff), "check", "--no-cache", "--output-format", "json", "."], tmp_path, 30)
    assert res.returncode == 1 and "F401" in res.stdout and not res.timed_out


def test_approval_policy():
    assert not ApprovalPolicy().approve(["ruff"], "x")              # default deny (non-interactive)
    assert ApprovalPolicy(trusted=True).approve(["ruff"], "x")
    seen = []
    pol = ApprovalPolicy(prompt=lambda m: seen.append(m) or True)
    assert pol.approve(["npm", "audit"], "audit", network=True, runs_repo_code=True)
    assert "npm audit" in seen[0] and "network" in seen[0] and "repository" in seen[0]
    assert not ApprovalPolicy(prompt=lambda m: False).approve(["ruff"], "x")


# ---------------------------------------------------------------- parsers
def test_parse_ruff(pctx, repo):
    out = json.dumps([
        {"code": "E722", "message": "Do not use bare `except`", "filename": str(repo / "pkg/a.py"),
         "location": {"row": 6, "column": 5}, "fix": None},
        {"code": "F821", "message": "Undefined name `zz`", "filename": str(repo / "pkg/a.py"), "location": {"row": 3}},
        {"code": "F401", "message": "unused", "filename": str(repo / "ignored.py"), "location": {"row": 1}},  # gitignored
        {"code": "F401", "message": "outside", "filename": "/etc/passwd", "location": {"row": 1}},          # escapes repo
        {"code": "F401", "message": "venv", "filename": str(repo / ".venv/lib/x.py"), "location": {"row": 1}},
    ])
    fs = parse_ruff(out, pctx)
    assert [f.title.split(":")[0] for f in fs] == ["F821", "E722"]  # sorted by severity
    assert fs[0].severity == Severity.HIGH and fs[1].severity == Severity.LOW
    assert fs[1].evidence == "except:" and fs[1].file_path == "pkg/a.py" and fs[1].line == 6
    assert all(f.verification == Verification.VERIFIED and f.agent == "tool:ruff" for f in fs)


def test_ruff_severity_mapping():
    assert ruff_severity("E902") == Severity.HIGH and ruff_severity("B006") == Severity.MEDIUM
    assert ruff_severity("F401") == Severity.LOW and ruff_severity("I001") == Severity.LOW


def test_parse_bandit_redacts_snippets(pctx, repo):
    out = json.dumps({"results": [{
        "filename": str(repo / "pkg/a.py"), "line_number": 3, "issue_severity": "HIGH", "issue_confidence": "MEDIUM",
        "issue_text": "Possible hardcoded password: 'hunter2secret'", "test_id": "B105", "test_name": "hardcoded_password",
        "code": "3 password = 'hunter2secret'\n", "more_info": "https://bandit.readthedocs.io/x"}]})
    f = parse_bandit(out, pctx)[0]
    assert f.severity == Severity.HIGH and f.category == "security/bandit-b105"
    assert "hunter2secret" not in f.model_dump_json()


def test_parse_gitleaks_never_stores_secret_and_respects_ignores(pctx, repo):
    (repo / ".env").write_text("TOKEN=abc")
    out = json.dumps([
        {"RuleID": "aws-access-token", "Description": "AWS key", "File": ".env", "StartLine": 1,
         "Match": "AKIAABCDEFGHIJKLMNOP", "Secret": "REDACTED"},
        {"RuleID": "generic-api-key", "Description": "key", "File": "tests/t.py", "StartLine": 2, "Match": "REDACTED"},
        {"RuleID": "x", "Description": "ignored", "File": "ignored.py", "StartLine": 1, "Match": "REDACTED"},
    ])
    fs = parse_gitleaks(out, pctx)
    assert [f.file_path for f in fs] == [".env", "tests/t.py"]  # gitignored file dropped
    assert fs[0].severity == Severity.CRITICAL and fs[1].severity == Severity.LOW  # tests downgraded
    assert "AKIAABCDEFGHIJKLMNOP" not in "".join(f.model_dump_json() for f in fs)
    assert fs[0].evidence == "[REDACTED]"


def test_parse_eslint(pctx, repo):
    out = json.dumps([{"filePath": str(repo / "web.js"), "messages": [
        {"ruleId": "no-eval", "severity": 2, "message": "eval can be harmful.", "line": 2},
        {"ruleId": "no-var", "severity": 1, "message": "Unexpected var.", "line": 1, "fix": {"range": [0, 3]}},
        {"ruleId": None, "fatal": True, "severity": 2, "message": "Parsing error", "line": 1}]}])
    fs = parse_eslint(out, pctx)
    assert {f.title.split(":")[0] for f in fs} == {"no-eval", "no-var", "parse-error"}
    assert next(f for f in fs if "no-var" in f.title).severity == Severity.LOW
    assert next(f for f in fs if "no-eval" in f.title).evidence == "eval(x)"


def test_parse_tsc(pctx, repo):
    out = "web.js(2,1): error TS2304: Cannot find name 'zz'.\nnoise line\nweb.js(1,5): error TS1005: ';' expected.\n"
    fs = parse_tsc(out, pctx)
    assert {f.severity for f in fs} == {Severity.HIGH, Severity.MEDIUM} and len(fs) == 2


def test_parse_npm_audit(pctx, repo):
    (repo / "package.json").write_text("{}")
    out = json.dumps({"vulnerabilities": {
        "lodash": {"severity": "high", "isDirect": True, "range": "<4.17.21", "via": [{"title": "Prototype Pollution"}],
                   "fixAvailable": {"name": "lodash", "version": "4.17.21", "isSemVerMajor": False}},
        "minimist": {"severity": "moderate", "via": ["x"], "fixAvailable": False}}})
    fs = parse_npm_audit(out, pctx)
    assert {f.severity for f in fs} == {Severity.HIGH, Severity.MEDIUM}
    top = fs[0]
    assert "Upgrade lodash to 4.17.21" in top.suggested_fix and "Prototype Pollution" in top.evidence


def test_npm_audit_error_payload_raises(pctx):
    with pytest.raises(ToolOutputError, match="ENOLOCK"):
        parse_npm_audit(json.dumps({"error": {"code": "ENOLOCK", "summary": "ENOLOCK needs lockfile"}}), pctx)


def test_invalid_json_raises_tool_output_error(pctx):
    for fn in (parse_ruff, parse_bandit, parse_gitleaks, parse_eslint):
        with pytest.raises(ToolOutputError):
            fn("not json", pctx)
    assert parse_ruff("", pctx) == []


def test_per_tool_cap(pctx, repo):
    items = [{"code": "F401", "message": f"m{i}", "filename": str(repo / "pkg/a.py"), "location": {"row": 1}}
             for i in range(MAX_FINDINGS_PER_TOOL + 25)]
    fs = parse_ruff(json.dumps(items), pctx)
    assert len(fs) == MAX_FINDINGS_PER_TOOL and pctx.omitted == 25


# ---------------------------------------------------------------- suite behaviour
def fake_tool(tmp_path, name, body):
    """A fake allowlisted executable on disk (a Python script) used via spec override."""
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(0o755)
    return path


def test_missing_tool_is_explained(repo, make_ctx, monkeypatch):
    monkeypatch.setattr(sa, "resolve_executable", lambda spec, root: None)
    c = make_ctx(repo)
    suite = sa.ToolSuite(ApprovalPolicy(trusted=True))
    findings, runs, _ = suite.run("analyze", repo, c.profile, c.reader)
    by = {r.name: r for r in runs}
    assert findings == [] and by["ruff"].status == "skipped" and "pip install ruff" in by["ruff"].detail
    assert "gitleaks" in by["gitleaks"].detail or "Install" in by["gitleaks"].detail
    assert by["eslint"].detail.startswith("not applicable") and "ESLint configuration" in by["eslint"].detail


def test_denied_without_approval_and_nothing_executed(repo, make_ctx, monkeypatch):
    called = []
    monkeypatch.setattr(sa, "run_command", lambda *a, **k: called.append(a))
    monkeypatch.setattr(sa, "resolve_executable", lambda spec, root: sa.Resolved("/usr/bin/" + spec.exe, False))
    c = make_ctx(repo)
    _, runs, _ = sa.ToolSuite(ApprovalPolicy()).run("quality", repo, c.profile, c.reader)
    assert called == [] and all(r.status in ("denied", "skipped") for r in runs)
    assert any(r.status == "denied" and "not approved" in r.detail for r in runs)


def test_prompt_shows_exact_command_and_project_local_warning(repo, make_ctx, monkeypatch):
    (repo / "eslint.config.js").write_text("export default []\n")
    bin_dir = repo / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "eslint").write_text("#!/bin/sh\n")
    messages = []
    monkeypatch.setattr(sa, "run_command", lambda *a, **k: pytest.fail("must not run when denied"))
    c = make_ctx(repo)
    sa.ToolSuite(ApprovalPolicy(prompt=lambda m: messages.append(m) and False)).run("quality", repo, c.profile, c.reader)
    msg = next(m for m in messages if "eslint" in m)
    assert "node_modules/.bin/eslint" in msg and "--no-fix" in msg and "executes code from this repository" in msg


def test_exit_code_timeout_and_garbage_output_are_reported_not_hidden(repo, make_ctx, monkeypatch):
    c = make_ctx(repo)
    monkeypatch.setattr(sa, "resolve_executable", lambda spec, root: sa.Resolved("/x/" + spec.exe, False))
    from repo_doctor.tools.command_runner import CommandResult
    for res, expect in [(CommandResult(["ruff"], 2, "", "boom: bad config"), "exit code 2: boom"),
                        (CommandResult(["ruff"], None, "", "", timed_out=True), "timed out"),
                        (CommandResult(["ruff"], 1, "<<<garbage>>>", ""), "not valid JSON")]:
        monkeypatch.setattr(sa, "run_command", lambda *a, _r=res, **k: _r)
        _, runs, _ = sa.ToolSuite(ApprovalPolicy(trusted=True), sa.ToolSettings(disabled=["bandit", "gitleaks"])).run(
            "quality", repo, c.profile, c.reader)
        ruff = next(r for r in runs if r.name == "ruff")
        assert ruff.status == "failed" and expect in ruff.detail


def test_disabled_tools_are_not_selected():
    suite = sa.ToolSuite(ApprovalPolicy(), sa.ToolSettings(disabled=["ruff"]))
    assert "ruff" not in [s.name for s, _, _ in suite.selected("quality")]
    assert suite.selected("tests") == []


def test_gitleaks_command_always_redacts(repo, make_ctx, monkeypatch):
    seen = {}
    monkeypatch.setattr(sa, "resolve_executable", lambda spec, root: sa.Resolved("/x/" + spec.exe, False))

    def fake_run(argv, cwd, timeout):
        seen["argv"] = argv
        Path(argv[argv.index("--report-path") + 1]).write_text("[]")
        from repo_doctor.tools.command_runner import CommandResult
        return CommandResult(argv, 0, "", "")

    monkeypatch.setattr(sa, "run_command", fake_run)
    c = make_ctx(repo)
    _, runs, _ = sa.ToolSuite(ApprovalPolicy(trusted=True), sa.ToolSettings(disabled=["bandit", "npm-audit"])).run(
        "security", repo, c.profile, c.reader)
    assert "--redact" in seen["argv"]
    assert not Path(seen["argv"][seen["argv"].index("--report-path") + 1]).exists()  # temp report cleaned up
    assert next(r for r in runs if r.name == "gitleaks").status == "ran"


# ---------------------------------------------------------------- real tools end to end
@pytest.mark.skipif(not (Path(sys.executable).parent / "ruff").exists(), reason="ruff not installed")
def test_real_ruff_through_suite(repo, make_ctx):
    c = make_ctx(repo)
    suite = sa.ToolSuite(ApprovalPolicy(trusted=True), sa.ToolSettings(disabled=["bandit", "gitleaks"]))
    fs, runs, _ = suite.run("quality", repo, c.profile, c.reader)
    ruff = next(r for r in runs if r.name == "ruff")
    assert ruff.status == "ran", ruff.detail
    titles = " ".join(f.title for f in fs)
    assert "F401" in titles and "E722" in titles
    assert all(f.file_path != "ignored.py" for f in fs)  # gitignored file excluded


@pytest.mark.skipif(not (Path(sys.executable).parent / "bandit").exists(), reason="bandit not installed")
def test_real_bandit_through_suite(tmp_path, make_ctx):
    (tmp_path / "x.py").write_text("import subprocess\nsubprocess.call('ls', shell=True)\n")
    c = make_ctx(tmp_path)
    suite = sa.ToolSuite(ApprovalPolicy(trusted=True), sa.ToolSettings(disabled=["gitleaks", "npm-audit"]))
    fs, runs, _ = suite.run("security", tmp_path, c.profile, c.reader)
    assert next(r for r in runs if r.name == "bandit").status == "ran"
    assert any("B602" in f.title or "B404" in f.title for f in fs)


# ---------------------------------------------------------------- orchestrator integration
def test_tool_and_builtin_findings_on_same_line_are_merged(tmp_path, make_ctx, monkeypatch):
    (tmp_path / "a.py").write_text("def f():\n    try:\n        pass\n    except:\n        pass\n")
    c = make_ctx(tmp_path)
    ruff = Finding(id="t", agent="tool:ruff", category="quality/ruff-e722", severity=Severity.LOW,
                   title="E722: bare except", description="d", file_path="a.py", line=4, evidence="except:",
                   confidence=0.95, verification=Verification.VERIFIED)
    suite = sa.ToolSuite(ApprovalPolicy(trusted=True))
    monkeypatch.setattr(suite, "run", lambda *a, **k: ([ruff], [], []))
    monkeypatch.setattr(suite, "selected", lambda mode: [object()])
    r = Orchestrator(c, tools=suite).run("quality")
    bare = [f for f in r.findings if f.line == 4]
    assert len(bare) == 1 and bare[0].agent == "code, tool:ruff" and bare[0].title.startswith("E722")
    assert r.duplicates_merged == 1


def test_total_steps_counts_tool_step(repo, make_ctx):
    c = make_ctx(repo)
    assert Orchestrator(c).total_steps("analyze") == 4
    assert Orchestrator(c, tools=sa.ToolSuite(ApprovalPolicy())).total_steps("analyze") == 5
    assert Orchestrator(c, tools=sa.ToolSuite(ApprovalPolicy())).total_steps("tests") == 2


# ---------------------------------------------------------------- explainer
def tool_finding(**kw):
    base = {"id": "F-001", "agent": "tool:ruff", "category": "quality/ruff-f401", "severity": Severity.LOW,
            "title": "F401: unused", "description": "d", "file_path": "pkg/a.py", "line": 1,
            "evidence": "import os", "confidence": 0.95, "verification": Verification.VERIFIED}
    return Finding(**{**base, **kw})


def test_explainer_adds_text_but_never_changes_evidence(repo, make_ctx):
    c = make_ctx(repo)
    c.provider = FakeProvider({"explanations": [{"id": "F-001", "explanation": "Unused import clutters code.",
                                                 "suggested_fix": "Remove it.", "severity": "critical",
                                                 "evidence": "HACKED", "line": 99}]})
    before = tool_finding()
    after = explain_tool_findings(c, [before])[0]
    assert after.explanation == "Unused import clutters code." and after.suggested_fix == "Remove it."
    for field in ("evidence", "severity", "line", "title", "file_path", "verification", "confidence"):
        assert getattr(after, field) == getattr(before, field)


def test_explainer_keeps_tool_fix_and_skips_non_tool_findings(repo, make_ctx):
    c = make_ctx(repo)
    c.provider = FakeProvider({"explanations": [{"id": "F-001", "explanation": "x", "suggested_fix": "model fix"},
                                                {"id": "F-002", "explanation": "y"}]})
    out = explain_tool_findings(c, [tool_finding(suggested_fix="tool fix"), tool_finding(id="F-002", agent="code")])
    assert out[0].suggested_fix == "tool fix" and out[1].explanation == ""


def test_explainer_prompt_redacted_and_failures_tolerated(tmp_path, make_ctx):
    (tmp_path / "a.py").write_text('password = "hunter2secret"\n')
    c = make_ctx(tmp_path)
    c.provider = FakeProvider({"explanations": []})
    explain_tool_findings(c, [tool_finding(file_path="a.py", line=1)])
    assert "hunter2secret" not in c.provider.prompts[0]
    c.provider = FakeProvider("garbage")
    assert explain_tool_findings(c, [tool_finding(file_path="a.py", line=1)])[0].explanation == ""
    assert any("explainer" in w for w in c.warnings)
    c.provider = None
    assert explain_tool_findings(c, [tool_finding()])[0].explanation == ""

