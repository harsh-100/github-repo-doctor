"""repo-doctor analyze --repo <github url>: mocked GitHub + git, real analysis pipeline and reports."""
import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from reporting import report_data, run_dir
from typer.testing import CliRunner

from repo_doctor.cli import app
from repo_doctor.reports.workspace import find_workspace, list_runs
from repo_doctor.sources.github import ClonedRepo, GitHubRef, RepoMeta, SourceError

runner = CliRunner()
URL = "https://github.com/octocat/sample-app"
META = RepoMeta(full_name="octocat/sample-app", default_branch="main", description="A sample app", size_kb=2048, stars=12,
                license="MIT", pushed_at="2026-11-20T00:00:00Z", language="Python", topics=["demo"])
README = "# Sample\n\nSample app is a tiny Flask service that greets visitors with a friendly message every time.\n"


@pytest.fixture
def remote(monkeypatch, sample_repo):
    """Fake GitHub: metadata from RepoMeta, 'clone' = copy of the sample project into a fresh temp dir (tracked)."""
    state = {"cloned": [], "meta_calls": 0, "removed": []}
    (sample_repo / "README.md").write_text(README)

    monkeypatch.setattr("repo_doctor.cli.fetch_repo_meta", lambda ref, timeout: (state.__setitem__("meta_calls", state["meta_calls"] + 1), META)[1])

    @contextmanager
    def fake_clone(ref, meta, settings):
        tmp = Path(tempfile.mkdtemp(prefix="repo-doctor-"))
        dest = tmp / ref.repo
        shutil.copytree(sample_repo, dest)
        state["cloned"].append(ref.full_name)
        try:
            yield ClonedRepo(dest, "ab" * 20, ref, meta)
        finally:
            shutil.rmtree(tmp)
            state["removed"].append(str(tmp))
            state["last_tmp"] = str(tmp)

    monkeypatch.setattr("repo_doctor.cli.cloned_repository", fake_clone)
    return state


def run(out, *extra, input=None, url=URL):
    return runner.invoke(app, ["analyze", "--repo", url, "--out", str(out), "--no-llm", "--no-tools", *extra], input=input)


# ---------------------------------------------------------------- the workflow
def test_explains_then_confirms_then_analyses_and_cleans_up(remote, tmp_path):
    out = tmp_path / "reports"
    r = run(out, input="y\n")
    assert r.exit_code == 0, r.output
    text = " ".join(r.output.split())
    for must in ("Public Repository Analysis", "GitHub URL validated", "Repository accessibility checked",
                 "Repository: octocat/sample-app", "Source: GitHub", "Visibility: Public", "What will happen:",
                 "Repository code will not be executed automatically.", "Continue? [y/N]", "Repository cloned (commit abababab)",
                 "Project structure discovered", "Temporary clone removed", "Analysis completed successfully!"):
        assert must in text, must
    assert remote["cloned"] == ["octocat/sample-app"] and len(remote["removed"]) == 1
    assert not Path(remote["last_tmp"]).exists()


def test_declining_the_prompt_clones_nothing(remote, tmp_path):
    out = tmp_path / "reports"
    for answer in ("n\n", ""):  # explicit no, and EOF (no terminal)
        r = run(out, input=answer)
        assert r.exit_code == 1 and "Cancelled. Nothing was cloned." in " ".join(r.output.split())
    assert remote["cloned"] == [] and not out.exists()


def test_yes_skips_the_prompt(remote, tmp_path):
    r = run(tmp_path / "reports", "--yes")
    assert r.exit_code == 0 and "Continue?" not in r.output and remote["cloned"]


def test_stage_lines_report_what_actually_happened_in_order(remote, tmp_path):
    r = run(tmp_path / "reports", "--yes")
    lines = [ln.strip() for ln in r.output.splitlines() if re.match(r"\s*\[(✓|●|✗)\]", ln)]
    seq = [re.sub(r"^\[.\] ", "", ln) for ln in lines]
    order = ["GitHub URL validated", "Repository accessibility checked", "Cloning repository...", "Repository cloned",
             "Project structure discovered", "Understanding project functionality...", "Code quality analysis...",
             "Security analysis...", "Testing analysis...", "Reviewer verification...", "Temporary clone removed"]
    pos = [next(i for i, s in enumerate(seq) if s.startswith(o)) for o in order]
    assert pos == sorted(pos), seq                      # stages appear in the order they ran
    assert re.search(r"\[5/5\] Reviewer Agent\s+DONE", r.output)
    assert lines[0].startswith("[✓]") and "[●] Cloning" in r.output


def test_reports_go_to_the_project_workspace_with_source_details(remote, tmp_path):
    out = tmp_path / "reports"
    run(out, "--yes")
    ws = out / "sample-app"
    assert ws.is_dir() and not any(p.name.startswith("repo-doctor-") for p in out.iterdir())
    root = Path(remote["last_tmp"]) / "sample-app"
    rd = next((ws / "runs").iterdir())
    meta = json.loads((rd / "metadata.json").read_text())
    assert meta["source"] == {"kind": "github", "url": URL, "owner": "octocat", "repo": "sample-app", "branch": "main",
                              "commit": "ab" * 20} and meta["root_hash"] == ""
    data = json.loads((rd / "findings.json").read_text())
    assert data["project"]["name"] == "sample-app" and data["project"]["root"] == "" and data["result"]["root"] == ""
    md, html = (rd / "report.md").read_text(), (rd / "report.html").read_text()
    for doc in (md, html):
        assert "octocat/sample-app" in doc and "abababab" in doc and "A sample app" in doc
        assert str(root) not in doc and "/tmp/repo-doctor-" not in doc
    assert "vscode://file/" not in html                        # no local path to link to
    assert "temporary shallow clone" in md and "Nothing from it was executed" in md
    for f in (rd / "metadata.json", rd / "findings.json", ws / "project.json"):
        assert str(root.parent) not in f.read_text()


def test_the_same_repository_always_lands_in_the_same_workspace(remote, tmp_path):
    out = tmp_path / "reports"
    run(out, "--yes")
    run(out, "--yes")
    assert [p.name for p in out.iterdir()] == ["sample-app"]
    ws = find_workspace(out, tmp_path, identity=("git:github.com/octocat/sample-app", "github.com/octocat/sample-app"))
    assert ws and len(list_runs(ws)) == 2
    other = run(out, "--yes", url="https://github.com/someone-else/sample-app")
    assert other.exit_code == 0 and min(p.name for p in out.iterdir()) == "sample-app" and len(list(out.iterdir())) == 2


def test_fix_does_not_apply_to_a_remote_analysis(remote, tmp_path):
    out = tmp_path / "reports"
    run(out, "--yes")
    r = runner.invoke(app, ["fix", "--path", str(tmp_path), "--out", str(out), "--list"])
    assert r.exit_code == 1  # there is no local checkout to edit


# ---------------------------------------------------------------- understanding in both formats
def test_understanding_is_in_markdown_and_html_with_the_same_content(remote, tmp_path):
    out = tmp_path / "reports"
    run(out, "--yes")
    rd = next((out / "sample-app" / "runs").iterdir())
    data = json.loads((rd / "findings.json").read_text())
    u = data["result"]["understanding"]
    md, html = (rd / "report.md").read_text(), (rd / "report.html").read_text()
    assert u["what"].startswith("Sample app is a tiny Flask service") and u["what_source"] == "docs"
    assert "## 2. Understanding this project" in md and 'id="understanding"' in html
    assert md.index("## 1. Project overview") < md.index("## 2. Understanding") < md.index("## 3. Executive summary")
    assert html.index('id="overview"') < html.index('id="understanding"') < html.index('id="summary"')
    for q in ("What is this project?", "What problem does it solve?", "What can you do with it?", "Who can benefit from it?",
              "What are the main technologies?", "How does it work?", "How can you get started?", "How mature does it look?"):
        assert q in md and q in html.replace("&#x27;", "'"), q
    for doc in (md, html):
        assert "Sample app is a tiny Flask service" in doc and u["maturity"]["level"].capitalize() in doc
        assert "not an official" in doc and "has not run" in doc.lower().replace("repo doctor ", "")
    assert all(t["name"] in md and t["name"] in html for t in u["technologies"])


def test_report_for_other_modes_explains_the_understanding_section_is_for_full_analysis(sample_repo, tmp_path):
    out = tmp_path / "reports"
    r = runner.invoke(app, ["security", "--path", str(sample_repo), "--no-llm", "--no-tools", "--out", str(out)])
    assert r.exit_code == 0
    rd = run_dir(out, sample_repo)
    assert "part of full analyses" in (rd / "report.md").read_text() and "part of full analyses" in (rd / "report.html").read_text()
    assert report_data(out, sample_repo)["result"]["understanding"] is None


# ---------------------------------------------------------------- failures are clear and leave nothing behind
@pytest.mark.parametrize("url,message", [
    ("https://gitlab.com/o/r", "Unsupported host"),
    ("git@github.com:o/r.git", "Only HTTPS"),
    ("https://github.com/o", "must point to a repository"),
    ("https://user:tok@github.com/o/r", "credentials"),
    ("not a url", "does not look like a GitHub URL"),
])
def test_invalid_urls_fail_before_anything_happens(remote, tmp_path, url, message):
    out = tmp_path / "reports"
    r = run(out, "--yes", url=url)
    assert r.exit_code == 1 and message in " ".join(r.output.split()) and "[✗]" in r.output
    assert remote["cloned"] == [] and remote["meta_calls"] == 0 and not out.exists()


@pytest.mark.parametrize("error", ["Repository o/r was not found, or it is private.", "Could not reach GitHub (ConnectError)."])
def test_missing_repository_and_network_errors(remote, monkeypatch, tmp_path, error):
    monkeypatch.setattr("repo_doctor.cli.fetch_repo_meta", lambda ref, t: (_ for _ in ()).throw(SourceError(error)))
    out = tmp_path / "reports"
    r = run(out, "--yes")
    assert r.exit_code == 1 and error in " ".join(r.output.split()) and remote["cloned"] == [] and not out.exists()


def test_clone_failure_and_timeout_report_cleanly_and_leave_nothing(monkeypatch, remote, tmp_path):
    @contextmanager
    def failing(ref, meta, settings):
        raise SourceError("Cloning took longer than 180s and was stopped.")
        yield  # pragma: no cover

    monkeypatch.setattr("repo_doctor.cli.cloned_repository", failing)
    out = tmp_path / "reports"
    r = run(out, "--yes")
    assert r.exit_code == 1 and "[✗] Cloning took longer than 180s" in " ".join(r.output.split())
    assert not out.exists() and "Analysis completed" not in r.output


def test_analysis_crash_still_removes_the_clone(remote, monkeypatch, tmp_path):
    monkeypatch.setattr("repo_doctor.cli.publish_run", lambda **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    r = run(tmp_path / "reports", "--yes")
    assert r.exit_code != 0 and remote["removed"] and not Path(remote["last_tmp"]).exists()


def test_rate_limited_api_falls_back_to_git(remote, monkeypatch, tmp_path):
    checked = []
    monkeypatch.setattr("repo_doctor.cli.fetch_repo_meta", lambda ref, t: RepoMeta(full_name=ref.full_name, api_available=False))
    monkeypatch.setattr("repo_doctor.cli.check_via_git", lambda ref, t: checked.append(ref.full_name))
    r = run(tmp_path / "reports", "--yes")
    assert r.exit_code == 0 and checked == ["octocat/sample-app"] and "rate limit" in " ".join(r.output.split())


def test_path_and_repo_together_are_rejected(remote, sample_repo, tmp_path):
    r = run(tmp_path / "reports", "--yes", "--path", str(sample_repo))
    assert r.exit_code == 1 and "either --path or --repo" in " ".join(r.output.split()) and remote["cloned"] == []


# ---------------------------------------------------------------- local analysis is unchanged
def test_local_path_analysis_still_works_and_shows_no_github_text(sample_repo, tmp_path):
    out = tmp_path / "reports"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--no-llm", "--no-tools", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "Public Repository Analysis" not in r.output and "Continue?" not in r.output
    assert "[5/5] Reviewer Agent" in r.output and "Analysis completed successfully!" in r.output
    data = report_data(out, sample_repo)
    assert data["project"]["source"] is None and data["project"]["root"] == str(sample_repo.resolve())
    assert "## 2. Understanding this project" in (run_dir(out, sample_repo) / "report.md").read_text()


def test_default_path_is_the_current_directory(sample_repo, tmp_path, monkeypatch):
    monkeypatch.chdir(sample_repo)
    r = runner.invoke(app, ["analyze", "--no-llm", "--no-tools", "--out", str(tmp_path / "reports")])
    assert r.exit_code == 0 and "Repository: sample" in r.output


def test_other_commands_accept_repo_option(remote, monkeypatch, tmp_path):
    from fakes import FakeProvider
    monkeypatch.setattr("repo_doctor.cli.create_provider", lambda s: FakeProvider("It greets visitors."))
    r = runner.invoke(app, ["explain", "--repo", URL, "--yes", "--out", str(tmp_path / "r")])
    assert r.exit_code == 0 and "It greets visitors." in r.output and remote["removed"]
    r = runner.invoke(app, ["security", "--repo", URL, "--yes", "--no-llm", "--no-tools", "--out", str(tmp_path / "r")])
    assert r.exit_code == 0


# ---------------------------------------------------------------- a failing step is reported as failed, never as done
def test_failed_understanding_is_reported_not_faked(sample_repo, monkeypatch, tmp_path):
    monkeypatch.setattr("repo_doctor.core.orchestrator.understand", lambda ctx, name: (_ for _ in ()).throw(RuntimeError("kaboom")))
    out = tmp_path / "reports"
    r = runner.invoke(app, ["analyze", "--path", str(sample_repo), "--no-llm", "--no-tools", "--out", str(out)])
    assert r.exit_code == 0
    assert re.search(r"\[1/5\] Project Understanding\s+FAILED", r.output) and "Agent 'understanding' failed" in r.output
    assert "Understanding this project" in (run_dir(out, sample_repo) / "report.md").read_text()
    assert report_data(out, sample_repo)["result"]["agent_errors"]["understanding"].startswith("RuntimeError")


def test_orchestrator_progress_events_are_accurate(sample_repo, make_ctx):
    from repo_doctor.core.orchestrator import Orchestrator
    events = []
    Orchestrator(make_ctx(sample_repo)).run("analyze", lambda s, st: events.append((s, st)))
    assert events == [("understanding", "running"), ("understanding", "done"), ("code", "running"), ("code", "done"),
                      ("security", "running"), ("security", "done"), ("testing", "running"), ("testing", "done"),
                      ("reviewer", "running"), ("reviewer", "done")]
    events.clear()
    Orchestrator(make_ctx(sample_repo)).run("security", lambda s, st: events.append((s, st)))
    assert ("understanding", "running") not in events          # focused modes do not run the understanding step


def test_github_ref_fields():
    assert GitHubRef("o", "r").full_name == "o/r"


def test_report_command_finds_a_github_analysis_by_url(remote, tmp_path):
    out = tmp_path / "reports"
    assert runner.invoke(app, ["report", "--repo", URL, "--out", str(out), "--json"]).exit_code == 1  # nothing analysed yet
    run(out, "--yes")
    r = runner.invoke(app, ["report", "--repo", URL + ".git", "--out", str(out), "--json"])
    assert r.exit_code == 0, r.output
    paths = json.loads(r.output)
    assert paths["project"] == "sample-app" and Path(paths["html"]).is_file() and paths["run_id"]
    assert runner.invoke(app, ["report", "--repo", "https://gitlab.com/o/r", "--out", str(out)]).exit_code == 1
