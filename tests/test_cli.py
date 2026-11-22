import json
from pathlib import Path

import httpx
import pytest
from reporting import report_md, run_dir
from reporting import result as run_result
from typer.testing import CliRunner

from repo_doctor.cli import app

runner = CliRunner()


def test_analyze_no_llm_writes_report(sample_repo, tmp_path):
    out = tmp_path / "out"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--no-llm", "--out", str(out)])
    assert r.exit_code == 0, r.output
    text = report_md(out, sample_repo)
    assert "Flask" in text and "React" in text and "Python (2 files)" in text
    # secrets in the fixture must never reach the report
    assert "hunter2secret" not in text and "abcdef123456" not in text


def test_analyze_bad_path_fails_gracefully(tmp_path):
    r = runner.invoke(app, ["analyze", "--path", str(tmp_path / "missing")])
    assert r.exit_code == 1 and "does not exist" in r.output


def test_analyze_with_mocked_llm_sends_redacted_context(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    sent = {}

    def fake_post(url, **kw):
        sent.update(kw["json"])
        return httpx.Response(200, json={"content": [{"type": "text", "text": "### Project description\nMocked."}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = tmp_path / "out"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "Mocked." in report_md(out, sample_repo)
    payload = str(sent)
    assert "hunter2secret" not in payload and "sk-ant-abcdefghijklmnop" not in payload


def test_analyze_survives_llm_failure(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(500, text="oops"))
    out = tmp_path / "out"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--out", str(out)])
    assert r.exit_code == 0 and (run_dir(out, sample_repo) / "report.md").exists()
    assert "AI summary failed" in r.output


def test_explain_without_key_fails(sample_repo):
    r = runner.invoke(app, ["explain", "--path", str(sample_repo)])
    assert r.exit_code == 1


def test_report_command(sample_repo, tmp_path):
    out = tmp_path / "out"
    r = runner.invoke(app, ["report", "--path", str(sample_repo), "--out", str(out)])
    assert r.exit_code == 1 and "No analysis of this project" in " ".join(r.output.split())
    runner.invoke(app, ["analyze", "--path", str(sample_repo), "--no-llm", "--no-tools", "--out", str(out)])
    shown = runner.invoke(app, ["report", "--path", str(sample_repo), "--out", str(out)])
    assert shown.exit_code == 0 and "Executive summary" in shown.output
    paths = json.loads(runner.invoke(app, ["report", "--path", str(sample_repo), "--out", str(out), "--json"]).output)
    assert paths["project"] == "sample" and paths["html"].endswith("report.html")
    assert paths["dashboard"].endswith("index.html") and Path(paths["markdown"]).is_file()


def test_setup_skip_check_stores_config(monkeypatch):
    r = runner.invoke(app, ["setup", "--provider", "openai", "--model", "m1", "--skip-check"],
                      input="\n")
    assert r.exit_code == 0, r.output
    from repo_doctor.core.settings import load_settings
    st = load_settings()
    assert (st.llm.provider, st.llm.model) == ("openai", "m1")


def test_setup_connectivity_failure_exits_nonzero(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": {"message": "no"}}))
    r = runner.invoke(app, ["setup", "--provider", "anthropic", "--model", "m"])
    assert r.exit_code == 1 and "Connectivity check failed" in r.output


def test_setup_rejects_unknown_provider():
    assert runner.invoke(app, ["setup", "--provider", "bogus"]).exit_code == 1


def test_analyze_writes_both_reports_and_findings_json(sample_repo, tmp_path):
    out = tmp_path / "out"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--no-llm", "--out", str(out)])
    assert r.exit_code == 0, r.output
    rd = run_dir(out, sample_repo)
    assert {p.name for p in rd.iterdir()} == {"report.md", "report.html", "findings.json", "metadata.json", "assets"}
    assert {p.name for p in (out / "sample").iterdir()} == {"index.html", "project.json", "README.md", "runs"}
    assert "Critical Issues:" in r.output and "Project Health: Critical" in r.output
    assert "[5/5] Reviewer Agent" in r.output and "Static Analysis" in r.output
    assert "denied" in r.output  # tools never run without approval
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text()
            assert "hunter2secret" not in text and "sk-ant-abcdefghijklmnop" not in text, f.name


@pytest.mark.parametrize("cmd,report,agent,last", [
    ("security", "SECURITY_REPORT.md", "Security Agent", 3),
    ("quality", "QUALITY_REPORT.md", "Code Analysis Agent", 3),
    ("tests", "TESTS_REPORT.md", "Testing Agent", 2),
])
def test_focused_commands(sample_repo, tmp_path, cmd, report, agent, last):
    out = tmp_path / "out"
    r = runner.invoke(app, [cmd, "--path", str(sample_repo), "--no-llm", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert (run_dir(out, sample_repo) / "report.md").exists()
    assert json.loads((run_dir(out, sample_repo) / "metadata.json").read_text())["mode"] == {
        "SECURITY_REPORT.md": "security", "QUALITY_REPORT.md": "quality", "TESTS_REPORT.md": "tests"}[report]
    assert agent in r.output and f"[{last}/{last}] Reviewer Agent" in r.output


def test_llm_notice_shown_and_agents_use_model(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls = []

    def fake_post(url, **kw):
        calls.append(kw["json"])
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"findings": [], "reviews": []}'}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--out", str(tmp_path / "o")])
    assert r.exit_code == 0, r.output
    assert "will be sent to" in " ".join(r.output.split()) and len(calls) >= 2
    assert "hunter2secret" not in str(calls)


def test_provider_outage_degrades_gracefully(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(500, text="oops"))
    out = tmp_path / "o"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--out", str(out)])
    assert r.exit_code == 0 and (run_dir(out, sample_repo) / "report.html").exists()
    assert "LLM batch failed" in report_md(out, sample_repo)
    assert "Critical Issues:  1" in r.output  # deterministic findings still delivered


# ---------------------------------------------------------------- fix command
FIX_CODE = "def load(path):\n    try:\n        return open(path).read()\n    except:\n        return None\n"
FIX_EDIT = {"search": "    except:\n        return None", "replace": "    except OSError:\n        return None",
            "reason": "catch only file errors"}


@pytest.fixture
def analysed(tmp_path, monkeypatch):
    """A project analysed with deterministic checks only; returns (root, out_dir, finding_id)."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "mod.py").write_text(FIX_CODE)
    out = tmp_path / "out"
    r = runner.invoke(app, ["quality", "--path", str(root), "--no-llm", "--no-tools", "--out", str(out)])
    assert r.exit_code == 0, r.output
    fid = next(f["id"] for f in run_result(out, root)["findings"] if f["title"] == "Bare except")
    return root, out, fid


def use_provider(monkeypatch, *replies):
    from fakes import FakeProvider
    prov = FakeProvider(*replies)
    monkeypatch.setattr("repo_doctor.cli.create_provider", lambda settings: prov)
    return prov


def run_fix(root, out, *extra, input=None):
    return runner.invoke(app, ["fix", "--path", str(root), "--out", str(out), "--no-tools", *extra], input=input)


def test_fix_approved_end_to_end(analysed, monkeypatch):
    root, out, fid = analysed
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    r = run_fix(root, out, "--id", fid, input="y\n")
    assert r.exit_code == 0, r.output
    assert "except OSError" in (root / "mod.py").read_text()
    assert "Apply this change" in r.output and "catch only file errors" in r.output and "-    except:" in r.output
    report = next(run_dir(out, root).glob("fix-report-*.md")).read_text()
    assert "Applied and verified: **1**" in report and "```diff" in report
    assert "Originals backed up to" in r.output


def test_fix_declined_changes_nothing(analysed, monkeypatch):
    root, out, fid = analysed
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    r = run_fix(root, out, "--id", fid, input="n\n")
    assert r.exit_code == 0 and (root / "mod.py").read_text() == FIX_CODE
    assert "Declined:" in r.output and "Originals backed up" not in r.output


def test_fix_without_terminal_input_defaults_to_no(analysed, monkeypatch):
    root, out, fid = analysed
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    run_fix(root, out, "--id", fid, input="")  # EOF at the prompt
    assert (root / "mod.py").read_text() == FIX_CODE


def test_fix_dry_run_shows_diff_never_prompts_or_writes(analysed, monkeypatch):
    root, out, fid = analysed
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    r = run_fix(root, out, "--id", fid, "--dry-run")
    assert r.exit_code == 0 and (root / "mod.py").read_text() == FIX_CODE
    assert "+    except OSError:" in r.output and "Apply this change" not in r.output and "Dry-run proposals" in r.output


def test_fix_interactive_selection(analysed, monkeypatch):
    root, out, _ = analysed
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    r = run_fix(root, out, input="1\ny\n")
    assert r.exit_code == 0, r.output
    assert "except OSError" in (root / "mod.py").read_text()
    cancelled = run_fix(root, out, input="\n")
    assert "Cancelled" in cancelled.output


def test_fix_rolls_back_and_exits_2_when_verification_fails(analysed, monkeypatch):
    root, out, fid = analysed
    still_bare = {"search": "        return None", "replace": "        return ''", "reason": "does not fix it"}
    use_provider(monkeypatch, {"edits": [still_bare]})
    r = run_fix(root, out, "--id", fid, input="y\n")
    assert r.exit_code == 2 and (root / "mod.py").read_text() == FIX_CODE
    assert "rolled back" in " ".join(r.output.split()) and "Rolled back:" in r.output


def test_fix_model_cannot_fix_is_reported_not_applied(analysed, monkeypatch):
    root, out, fid = analysed
    use_provider(monkeypatch, {"edits": [], "cannot_fix": "needs design decision"})
    r = run_fix(root, out, "--id", fid, input="y\n")
    assert r.exit_code == 0 and (root / "mod.py").read_text() == FIX_CODE
    assert "needs design decision" in " ".join(r.output.split()) and "Skipped:" in r.output


def test_fix_refuses_findings_for_another_repository(analysed, tmp_path, monkeypatch):
    _, out, fid = analysed
    other = tmp_path / "other"
    other.mkdir()
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    r = run_fix(other, out, "--id", fid)
    assert r.exit_code == 1 and "No analysis of this project" in " ".join(r.output.split())


def test_fix_requires_findings_and_a_provider(analysed, tmp_path):
    root, out, fid = analysed
    r = run_fix(root, tmp_path / "empty", "--id", fid)
    assert r.exit_code == 1 and "No analysis of this project" in " ".join(r.output.split())
    r = run_fix(root, out, "--id", fid)  # no API key configured
    assert r.exit_code == 1 and "requires an AI provider" in " ".join(r.output.split())
    assert (root / "mod.py").read_text() == FIX_CODE


def test_fix_secret_findings_are_not_auto_fixable(sample_repo, tmp_path, monkeypatch):
    out = tmp_path / "out"
    runner.invoke(app, ["security", "--path", str(sample_repo), "--no-llm", "--no-tools", "--out", str(out)])
    secret = next(f["id"] for f in run_result(out, sample_repo)["findings"]
                  if f["category"].startswith("security/hardcoded-secret"))
    before = (sample_repo / "src" / "app.py").read_text()
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    r = run_fix(sample_repo, out, "--id", secret, input="y\n")
    assert "not auto-fixable" in r.output and (sample_repo / "src" / "app.py").read_text() == before


def test_fix_unknown_id_warns(analysed, monkeypatch):
    root, out, _ = analysed
    use_provider(monkeypatch, {"edits": [FIX_EDIT]})
    r = run_fix(root, out, "--id", "F-999")
    assert "Unknown finding id" in r.output and "Nothing to fix" in r.output
