import pytest
from fakes import FakeProvider

from repo_doctor.agents.base import ask_findings, extract_json, locate_evidence
from repo_doctor.agents.code_agent import CodeAgent, python_checks
from repo_doctor.agents.security_agent import SecurityAgent, scan_file
from repo_doctor.agents.testing_agent import TestingAgent
from repo_doctor.core.models import Severity, Verification
from repo_doctor.providers import ProviderError

CODE = "import os\n\n\ndef risky(items=[]):\n    try:\n        return os.getcwd()\n    except:\n        pass\n"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(CODE)
    return tmp_path


def raw(**kw):
    base = {"category": "quality/x", "severity": "medium", "title": "T", "description": "d",
            "file_path": "pkg/mod.py", "line": 7, "evidence": "except:", "suggested_fix": "f", "confidence": 0.8}
    return {**base, **kw}


def ask(ctx, items, sent=None, **kw):
    ctx.provider = FakeProvider({"findings": items}) if not isinstance(items, FakeProvider) else items
    return ask_findings(ctx, "code", "quality", "sys", "prompt", sent or {"pkg/mod.py": (1, 8)}, **kw)


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    assert extract_json("no json") is None


def test_locate_evidence_prefers_nearest():
    lines = ["x = 1", "y = 2", "x = 1"]
    assert locate_evidence(lines, "x = 1", near=3) == 3
    assert locate_evidence(lines, "nothing here", None) is None
    assert locate_evidence(lines, "ab", None) is None  # too short to be meaningful


def test_invented_file_is_dropped(repo, make_ctx):
    ctx = make_ctx(repo)
    out = ask(ctx, [raw(file_path="pkg/ghost.py"), raw()])
    assert [f.file_path for f in out] == ["pkg/mod.py"]
    assert any("dropped 1" in n for n in ctx.notes)


def test_file_not_in_this_batch_is_dropped(repo, make_ctx):
    (repo / "pkg" / "other.py").write_text("x = 1\n")
    ctx = make_ctx(repo)
    assert ask(ctx, [raw(file_path="pkg/other.py")]) == []


def test_wrong_line_corrected_from_evidence(repo, make_ctx):
    out = ask(make_ctx(repo), [raw(line=2)])[0]
    assert out.line == 7 and out.verification == Verification.SUPPORTED


def test_out_of_range_line_dropped(repo, make_ctx):
    out = ask(make_ctx(repo), [raw(line=999, evidence="not in file at all")])[0]
    assert out.line is None


def test_unlocated_evidence_is_unverified_and_capped(repo, make_ctx):
    out = ask(make_ctx(repo), [raw(severity="critical", evidence="totally made up code", confidence=0.99)])[0]
    assert out.verification == Verification.UNVERIFIED
    assert out.severity == Severity.MEDIUM and out.confidence <= 0.3


def test_secrets_in_model_output_are_redacted(repo, make_ctx):
    out = ask(make_ctx(repo), [raw(evidence="except:", description="key sk-ant-abcdefghijklmnop12345 leaked",
                                   suggested_fix="rotate ghp_" + "a" * 36)])[0]
    assert "sk-ant-abcdef" not in out.description and "ghp_aaaa" not in out.suggested_fix


def test_bad_items_and_unknown_severity_tolerated(repo, make_ctx):
    out = ask(make_ctx(repo), ["garbage", {"no_title": 1}, raw(severity="catastrophic")])
    assert len(out) == 1 and out[0].severity == Severity.LOW


def test_invalid_json_retries_once_then_raises(repo, make_ctx):
    ctx = make_ctx(repo)
    p = FakeProvider("nope", "still nope")
    with pytest.raises(ProviderError, match="valid JSON"):
        ask(ctx, p)
    assert ctx.llm_calls == 2
    ctx2 = make_ctx(repo)
    ok = ask(ctx2, FakeProvider("nope", '{"findings": []}'))
    assert ok == [] and ctx2.llm_calls == 2


def test_python_checks_deterministic(repo):
    fs = python_checks("pkg/mod.py", CODE.split("\n"))
    titles = {f.title for f in fs}
    assert {"Bare except", "Mutable default argument"} <= titles
    assert all(f.verification == Verification.VERIFIED for f in fs)
    bare = next(f for f in fs if f.title == "Bare except")
    assert bare.line == 7


def test_python_syntax_error_and_redaction_safe():
    fs = python_checks("a.py", ["def broken(:"])
    assert fs[0].title == "Syntax error" and fs[0].line == 1


def test_code_agent_no_llm_runs_ast_only(repo, make_ctx):
    out = CodeAgent().run(make_ctx(repo))
    assert {f.title for f in out} >= {"Bare except"}


def test_code_agent_llm_findings_and_prompt_has_no_secrets(repo, make_ctx):
    (repo / "pkg" / "cfg.py").write_text('TOKEN = "supersecretvalue123"\nx = 1\n')
    p = FakeProvider({"findings": [raw(title="Swallowed exception")]})
    ctx = make_ctx(repo, p)
    out = CodeAgent().run(ctx)
    assert any(f.title == "Swallowed exception" for f in out)
    assert "supersecretvalue123" not in "".join(p.prompts)
    assert "   7| " in "".join(p.prompts)  # numbered listing sent


def test_code_agent_survives_failed_batch(repo, make_ctx):
    ctx = make_ctx(repo, FakeProvider(ProviderError("boom")))
    out = CodeAgent().run(ctx)
    assert out and any("boom" in w for w in ctx.warnings)


def test_call_limit_respected_and_coverage_noted(tmp_path, make_ctx):
    for i in range(6):
        (tmp_path / f"m{i}.py").write_text("x = 1\n" * 400)
    p = FakeProvider({"findings": []})
    ctx = make_ctx(tmp_path, p, max_llm_calls_per_agent=2, batch_chars=3000)
    CodeAgent().run(ctx)
    assert len(p.prompts) == 2
    assert any("of 6 source files" in n for n in ctx.notes)


def test_zero_call_limit_disables_llm(repo, make_ctx):
    p = FakeProvider({"findings": []})
    CodeAgent().run(make_ctx(repo, p, max_llm_calls_per_agent=0))
    assert p.prompts == []


# ---- security
def test_secret_scan_shapes_and_severity():
    lines = ['KEY = "sk-ant-abcdefghijklmnop1234567890"', 'DB = "postgres://u:realpass1@h/db"',
             'password = "hunter2secret"', 'password = os.environ["PW"]', 'token = "<your-token>"']
    fs = scan_file("a.py", "Python", "source", lines)
    assert [f.line for f in fs] == [1, 2, 3]
    assert fs[0].severity == Severity.CRITICAL
    assert all("sk-ant-abcdefghijklmnop" not in f.evidence and "hunter2secret" not in f.evidence for f in fs)
    test_fs = scan_file("t.py", "Python", "test", lines[:1])
    assert test_fs[0].severity == Severity.LOW


def test_risky_patterns_only_in_matching_languages_and_skip_comments():
    py = scan_file("a.py", "Python", "source", ["eval(x)", "# eval(y)", 'cur.execute(f"select {x}")'])
    assert {f.title for f in py} == {"Use of eval()", "Possible SQL injection (string-built query)"}
    js = scan_file("a.js", "JavaScript", "source", ["el.innerHTML = x", "os.system(x)"])
    assert [f.title for f in js] == ["Raw HTML injection sink"]


def test_private_key_detected():
    fs = scan_file("k.txt", None, "config", ["-----BEGIN RSA PRIVATE KEY-----", "MIIabc"])
    assert fs[0].severity == Severity.CRITICAL and fs[0].evidence == "[REDACTED]"


def test_security_agent_flags_ungitignored_env(sample_repo, make_ctx):
    out = SecurityAgent().run(make_ctx(sample_repo))
    env = [f for f in out if "Sensitive file" in f.title]
    assert env and env[0].file_path == ".env" and "abcdef123456" not in str(env[0])


def test_security_agent_gitignored_env_not_flagged(sample_repo, make_ctx):
    (sample_repo / ".gitignore").write_text(".env\n")
    out = SecurityAgent().run(make_ctx(sample_repo))
    assert not [f for f in out if "Sensitive file" in f.title]


def test_security_llm_prompt_is_redacted(sample_repo, make_ctx):
    p = FakeProvider({"findings": []})
    SecurityAgent().run(make_ctx(sample_repo, p))
    sent = "".join(p.prompts)
    assert "hunter2secret" not in sent and "sk-ant-abcdefghijklmnop" not in sent and "abcdef123456" not in sent


# ---- testing
def test_no_tests_detected(repo, make_ctx):
    out = TestingAgent().run(make_ctx(repo))
    assert out[0].category == "testing/no-tests" and out[0].verification == Verification.VERIFIED


def _big_module(path, n=20):
    path.write_text("\n".join(f"def f{i}():\n    return {i}" for i in range(n)))


def test_untested_module_detected_by_reference_not_filename(tmp_path, make_ctx):
    (tmp_path / "pkg").mkdir()
    _big_module(tmp_path / "pkg" / "billing.py")
    _big_module(tmp_path / "pkg" / "orders.py")
    (tmp_path / "tests").mkdir()
    # test file name is unconventional for 'orders' but it imports it -> not flagged
    (tmp_path / "tests" / "test_suite.py").write_text("from pkg import orders\n\ndef test_a():\n    assert orders\n")
    out = TestingAgent().run(make_ctx(tmp_path))
    names = [f.file_path for f in out]
    assert names == ["pkg/billing.py"]
    assert out[0].verification == Verification.SUPPORTED and out[0].confidence <= 0.5


def test_pytest_testpaths_config_respected(tmp_path, make_ctx):
    _big_module(tmp_path / "calc.py")
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "suite.py").write_text("import calc\n\ndef run():\n    calc.f1()\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["checks"]\n')
    out = TestingAgent().run(make_ctx(tmp_path))
    assert out == []  # not "no tests", and calc is referenced


def test_testing_agent_llm_suggestion(tmp_path, make_ctx):
    _big_module(tmp_path / "calc.py")
    (tmp_path / "test_other.py").write_text("def test_x():\n    pass\n")
    p = FakeProvider({"findings": [raw(file_path="calc.py", category="testing/edge", title="Add error-path tests",
                                       line=1, evidence="def f0():")]})
    out = TestingAgent().run(make_ctx(tmp_path, p))
    assert any(f.title == "Add error-path tests" and f.verification == Verification.SUPPORTED for f in out)


def test_risky_pattern_inside_string_literal_is_ignored():
    lines = ['msg = "eval() executes arbitrary code"', "x = eval(user_input)", "note = 'call exec(x) here'"]
    fs = scan_file("a.py", "Python", "source", lines)
    assert [(f.title, f.line) for f in fs] == [("Use of eval()", 2)]


def test_private_key_in_test_file_is_downgraded():
    fs = scan_file("tests/t.py", "Python", "test", ["k = '-----BEGIN RSA PRIVATE KEY-----'"])
    assert fs[0].severity == Severity.LOW
