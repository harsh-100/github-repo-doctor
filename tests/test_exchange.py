"""The exchange provider: Claude Code (simulated here) answers request files."""
import json
import re
from pathlib import Path

import pytest
from reporting import report_md
from reporting import result as run_result
from typer.testing import CliRunner

from repo_doctor.cli import app
from repo_doctor.providers import ExchangePending, ExchangeProvider, ProviderError
from repo_doctor.providers.exchange import clear_exchange, pending_requests, request_key

runner = CliRunner()
CODE = "def load(path):\n    try:\n        return open(path).read()\n    except:\n        return None\n"
FIX = {"edits": [{"search": "    except:\n        return None", "replace": "    except OSError:\n        return None",
                  "reason": "catch only file errors"}]}


# ---------------------------------------------------------------- provider
def test_request_written_then_pending_then_answered(tmp_path):
    p = ExchangeProvider(tmp_path / "ex")
    with pytest.raises(ExchangePending) as e:
        p.complete("sys", "hello")
    key = e.value.key
    assert key == request_key("sys", "hello") and isinstance(e.value, ProviderError)
    req = json.loads((tmp_path / "ex" / f"{key}.request.json").read_text())
    assert req["prompt"] == "hello" and req["response_path"].endswith(f"{key}.response.txt")
    assert [r["key"] for r in pending_requests(tmp_path / "ex")] == [key]
    (tmp_path / "ex" / f"{key}.response.txt").write_text("answer")
    assert p.complete("sys", "hello") == "answer" and pending_requests(tmp_path / "ex") == []


def test_requests_are_redacted_and_keys_are_stable(tmp_path):
    p = ExchangeProvider(tmp_path)
    for _ in range(2):
        with pytest.raises(ExchangePending):
            p.complete("s", 'password = "hunter2secret" key sk-ant-abcdefghijklmnop12345')
    files = list(tmp_path.glob("*.request.json"))
    assert len(files) == 1  # same prompt => same request, not duplicated
    text = files[0].read_text()
    assert "hunter2secret" not in text and "sk-ant-abcdefghijklmnop" not in text


def test_oversized_response_rejected_and_clear(tmp_path):
    p = ExchangeProvider(tmp_path)
    with pytest.raises(ExchangePending) as e:
        p.complete("s", "x")
    (tmp_path / f"{e.value.key}.response.txt").write_text("a" * 500_000)
    with pytest.raises(ProviderError, match="too large"):
        p.complete("s", "x")
    assert clear_exchange(tmp_path) == 2 and not list(tmp_path.iterdir())


# ---------------------------------------------------------------- the simulated Claude
def respond(out_dir, rules):
    """Answer every pending request with the first matching rule; returns how many were answered."""
    pend = json.loads(runner.invoke(app, ["exchange", "pending", "--out", str(out_dir)]).output)["pending"]
    for item in pend:
        req = json.loads(Path(item["request_file"]).read_text())
        text = req["system"] + "\n" + req["prompt"]
        for needle, reply in rules:
            if needle in text:
                Path(item["response_file"]).write_text(reply if isinstance(reply, str) else json.dumps(reply))
                break
        else:
            raise AssertionError(f"no rule for request: {text[:200]}")
    return len(pend)


def pending_count(out_dir):
    return len(json.loads(runner.invoke(app, ["exchange", "pending", "--out", str(out_dir)]).output)["pending"])


CODE_FINDING = {"findings": [{"category": "quality/error-handling", "severity": "medium", "title": "Swallowed error",
                              "description": "errors are hidden", "file_path": "mod.py", "line": 4,
                              "evidence": "except:", "suggested_fix": "narrow the exception", "confidence": 0.8}]}
RULES = [("skeptical reviewer", {"reviews": []}), ("Review this code", CODE_FINDING),
         ("Untested modules", {"findings": []}), ("explain static-analysis", {"explanations": []}),
         ("software architect", "### Project description\nTiny.")]


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "mod.py").write_text(CODE)
    return root


def analyze(root, out, *extra):
    return runner.invoke(app, ["analyze", "--path", str(root), "--out", str(out), "--provider", "exchange",
                               "--no-tools", *extra])


def test_analyze_converges_through_rounds(proj, tmp_path):
    out = tmp_path / "out"
    r = analyze(proj, out)
    assert r.exit_code == 0, r.output
    assert "waiting for answers" in " ".join(r.output.split())
    first = run_result(out, proj, include_provisional=True)
    assert not any(f["agent"] == "code" and f["verification"] == "supported" for f in first["findings"])  # provisional

    rounds = 0
    while pending_count(out) and rounds < 6:
        respond(out, RULES)
        r = analyze(proj, out)
        assert r.exit_code == 0, r.output
        rounds += 1
    assert pending_count(out) == 0 and 2 <= rounds <= 4

    final = run_result(out, proj)
    swallowed = [f for f in final["findings"] if f["title"] == "Swallowed error"]
    assert swallowed and swallowed[0]["verification"] == "supported" and swallowed[0]["line"] == 4
    assert "Tiny." in report_md(out, proj)


def test_model_answers_still_go_through_validation(proj, tmp_path):
    out = tmp_path / "out"
    analyze(proj, out)
    invented = {"findings": [{"category": "quality/x", "severity": "critical", "title": "Ghost", "description": "d",
                              "file_path": "ghost.py", "line": 1, "evidence": "x", "suggested_fix": "y"},
                             {"category": "quality/x", "severity": "critical", "title": "Unproven", "description": "d",
                              "file_path": "mod.py", "line": 2, "evidence": "totally invented code"}]}
    for _ in range(5):
        if not respond(out, [("skeptical reviewer", {"reviews": []}), ("Review this code", invented),
                             ("software architect", "x")]):
            break
        analyze(proj, out)
    titles = {f["title"]: f for f in run_result(out, proj)["findings"]}
    assert "Ghost" not in titles  # invented file dropped
    assert titles["Unproven"]["verification"] == "unverified" and titles["Unproven"]["severity"] == "medium"


def test_exchange_requests_never_contain_secrets(sample_repo, tmp_path):
    out = tmp_path / "out"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--out", str(out), "--provider", "exchange",
                            "--no-tools"])
    assert r.exit_code == 0, r.output
    reqs = list((out / ".exchange").glob("*.request.json"))
    assert reqs
    blob = "".join(f.read_text() for f in reqs)
    for secret in ("hunter2secret", "sk-ant-abcdefghijklmnop", "abcdef123456"):
        assert secret not in blob


def test_exchange_pending_and_clear_commands(proj, tmp_path):
    out = tmp_path / "out"
    analyze(proj, out)
    assert pending_count(out) > 0
    assert "Removed" in runner.invoke(app, ["exchange", "clear", "--out", str(out)]).output
    assert pending_count(out) == 0


# ---------------------------------------------------------------- fix with diff-bound approval
@pytest.fixture
def analysed(proj, tmp_path):
    out = tmp_path / "out"
    r = runner.invoke(app, ["quality", "--path", str(proj), "--out", str(out), "--no-llm", "--no-tools"])
    assert r.exit_code == 0, r.output
    fid = next(f["id"] for f in run_result(out, proj)["findings"] if f["title"] == "Bare except")
    return proj, out, fid


def fix(proj, out, fid, *extra, cmd="fix"):
    return runner.invoke(app, [cmd, "--path", str(proj), "--out", str(out), "--id", fid, "--provider", "exchange",
                               "--no-tools", *extra])


def apply(proj, out, fid, diff_id):
    return fix(proj, out, fid, "--approve-diff", diff_id, cmd="fix-apply")


def test_fix_via_exchange_needs_matching_diff_id(analysed):
    proj, out, fid = analysed
    r = fix(proj, out, fid, "--dry-run")
    assert "waiting for answers" in " ".join(r.output.split()) and (proj / "mod.py").read_text() == CODE
    assert respond(out, [("Finding to resolve", FIX)]) == 1

    r = fix(proj, out, fid, "--dry-run")                     # same prompt => same diff, now with the answer
    assert r.exit_code == 0 and "+    except OSError:" in r.output
    diff_id = re.search(r"Diff ID: (\w{12})", r.output).group(1)
    assert (proj / "mod.py").read_text() == CODE            # dry run never writes

    wrong = apply(proj, out, fid, "000000000000")
    assert "not applied" in " ".join(wrong.output.split()) and (proj / "mod.py").read_text() == CODE

    ok = apply(proj, out, fid, diff_id)
    assert ok.exit_code == 0, ok.output
    assert "except OSError" in (proj / "mod.py").read_text()
    assert "Applied (verified):" in ok.output
    backups = list((out.parent / "cfg" / "backups").rglob("mod.py"))
    assert backups and backups[0].read_text() == CODE


def test_approval_is_bound_to_exact_change(analysed):
    proj, out, fid = analysed
    fix(proj, out, fid, "--dry-run")
    respond(out, [("Finding to resolve", FIX)])
    diff_id = re.search(r"Diff ID: (\w{12})", fix(proj, out, fid, "--dry-run").output).group(1)
    (proj / "mod.py").write_text(CODE.replace("return None", "return ''"))   # file changes after approval was issued
    r = apply(proj, out, fid, diff_id)
    assert "return ''" in (proj / "mod.py").read_text() and "except OSError" not in (proj / "mod.py").read_text()
    assert r.exit_code == 0


def test_plain_fix_cannot_self_approve_and_fix_apply_needs_both_args(analysed):
    proj, out, fid = analysed
    r = fix(proj, out, fid, "--approve-diff", "abc")           # option must not exist on `fix`
    assert r.exit_code != 0 and (proj / "mod.py").read_text() == CODE
    for args in ([], ["--id", fid], ["--approve-diff", "abc"]):
        r = runner.invoke(app, ["fix-apply", "--path", str(proj), "--out", str(out), "--provider", "exchange", *args])
        assert r.exit_code == 1 and "requires --id and --approve-diff" in " ".join(r.output.split())


def test_exchange_show_and_answer_commands(proj, tmp_path):
    out = tmp_path / "out"
    analyze(proj, out)
    item = json.loads(runner.invoke(app, ["exchange", "pending", "--out", str(out)]).output)["pending"][0]
    key = item["key"]
    shown = runner.invoke(app, ["exchange", "show", key, "--out", str(out)]).output
    assert "untrusted" in shown and "=== REQUEST ===" in shown
    assert item["response_file"] in shown  # tells the responder exactly where to write
    r = runner.invoke(app, ["exchange", "answer", key, "--out", str(out)], input='{"findings": []}')
    assert r.exit_code == 0 and Path(item["response_file"]).read_text() == '{"findings": []}'


def test_exchange_answer_rejects_bad_keys_and_unknown_requests(proj, tmp_path):
    out = tmp_path / "out"
    analyze(proj, out)
    for bad in ("../../etc/passwd", "ZZZ", "0123456789abcdeX", "0123456789abcdef"):  # last one: valid shape, no request
        r = runner.invoke(app, ["exchange", "answer", bad, "--out", str(out)], input="x")
        assert r.exit_code == 1
    assert not list((out / ".exchange").glob("0123456789abcdef*"))
    key = json.loads(runner.invoke(app, ["exchange", "pending", "--out", str(out)]).output)["pending"][0]["key"]
    assert runner.invoke(app, ["exchange", "answer", key, "--out", str(out)], input="   \n").exit_code == 1  # empty
    assert runner.invoke(app, ["exchange", "show", "nothexnothexnoth", "--out", str(out)]).exit_code == 1


def test_provisional_runs_are_flagged_hidden_from_history_and_never_used_by_fix(proj, tmp_path):
    out = tmp_path / "out"
    analyze(proj, out)  # round 1: model requests pending => provisional
    ws = out / "proj"
    first = json.loads(next((ws / "runs").glob("*/metadata.json")).read_text())
    assert first["provisional"] is True
    assert "Provisional report" in next((ws / "runs").glob("*/report.md")).read_text()
    assert "Provisional report" in next((ws / "runs").glob("*/report.html")).read_text()

    r = runner.invoke(app, ["fix", "--path", str(proj), "--out", str(out), "--list"])   # nothing completed yet
    assert r.exit_code == 1 and "provisional" in " ".join(r.output.split())
    r = runner.invoke(app, ["report", "--path", str(proj), "--out", str(out), "--json"])
    assert r.exit_code == 1

    for _ in range(6):
        if not respond(out, RULES):
            break
        analyze(proj, out)
    metas = sorted((json.loads(p.read_text()) for p in (ws / "runs").glob("*/metadata.json")),
                   key=lambda m: m["created_at"])
    assert metas[-1]["provisional"] is False and all(m["provisional"] for m in metas[:-1]) and len(metas) >= 3
    html = (ws / "index.html").read_text()
    assert html.count("report.html") == 1 and "provisional run(s)" in html          # history shows the finished analysis only
    assert all((ws / "runs" / m["run_id"] / "report.md").exists() for m in metas)   # nothing deleted
    paths = json.loads(runner.invoke(app, ["report", "--path", str(proj), "--out", str(out), "--json"]).output)
    assert paths["run_id"] == metas[-1]["run_id"]
