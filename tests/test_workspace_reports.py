"""Project report workspaces, run history, and the Markdown/HTML reports (one source of truth)."""
import base64
import hashlib
import json
import re
from html.parser import HTMLParser

import pytest
from reporting import report_data, run_dir
from typer.testing import CliRunner

from repo_doctor.cli import app
from repo_doctor.reports.workspace import (
    find_or_create_workspace,
    find_workspace,
    latest_run,
    list_runs,
    new_run_dir,
    sanitize_remote,
    slugify,
)

runner = CliRunner()
BAD = ("hunter2secret", "sk-ant-abcdefghijklmnop", "abcdef123456")


def analyze(root, out, *extra, mode="analyze"):
    r = runner.invoke(app, [mode, "--path", str(root), "--no-llm", "--no-tools", "--out", str(out), *extra])
    assert r.exit_code == 0, r.output
    return r


def make_project(tmp_path, name="proj", code="x = 1\n"):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "mod.py").write_text(code)
    return root


# ---------------------------------------------------------------- workspace layout
def test_first_analysis_creates_workspace_and_run(sample_repo, tmp_path):
    out = tmp_path / "reports"
    r = analyze(sample_repo, out)
    ws = out / "sample"
    assert {p.name for p in ws.iterdir()} == {"index.html", "project.json", "README.md", "runs"}
    (run,) = list((ws / "runs").iterdir())
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{6}", run.name)
    assert {p.name for p in run.iterdir()} == {"report.md", "report.html", "findings.json", "metadata.json", "assets"}
    assert {p.name for p in (run / "assets").iterdir()} == {"severity.svg", "verification.svg", "area.svg"}
    out_text = " ".join(r.output.split())
    assert "Analysis completed successfully!" in out_text and "Project: sample" in out_text
    assert "Markdown:" in r.output and "HTML:" in r.output and "Project dashboard:" in r.output
    assert "Previous analysis reports" not in r.output  # nothing earlier to preserve yet


def test_repeated_analysis_never_overwrites_earlier_runs(tmp_path):
    root = make_project(tmp_path)
    out = tmp_path / "reports"
    analyze(root, out)
    first = run_dir(out, root)
    snapshot = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.iterdir() if p.is_file()}
    (root / "mod.py").write_text("try:\n    pass\nexcept:\n    pass\n")
    r = analyze(root, out)
    assert "Previous analysis reports have been preserved" in r.output
    second = run_dir(out, root)
    assert second != first and len(list((out / "proj" / "runs").iterdir())) == 2
    assert snapshot == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.iterdir() if p.is_file()}
    index = (out / "proj" / "index.html").read_text()
    assert first.name in index and second.name in index and "2 run(s)" in index


def test_many_runs_in_the_same_second_get_unique_ids(tmp_path):
    ws = find_or_create_workspace(tmp_path / "r", make_project(tmp_path))
    ids = {new_run_dir(ws)[0] for _ in range(30)}
    assert len(ids) == 30


def test_projects_with_same_folder_name_get_distinct_stable_workspaces(tmp_path):
    a, b = make_project(tmp_path / "x", "app", "a = 1\n"), make_project(tmp_path / "y", "app", "b = 2\n")
    out = tmp_path / "reports"
    analyze(a, out)
    analyze(b, out)
    names = sorted(p.name for p in out.iterdir())
    assert names[0] == "app" and re.fullmatch(r"app-[0-9a-f]{6}", names[1]) and len(names) == 2
    analyze(a, out)
    analyze(b, out)  # re-running reuses each workspace instead of creating new ones
    assert sorted(p.name for p in out.iterdir()) == names
    assert find_workspace(out, a).slug == "app" and find_workspace(out, b).slug == names[1]
    assert len(list_runs(find_workspace(out, a))) == 2 == len(list_runs(find_workspace(out, b)))


def test_same_git_remote_is_the_same_project_and_credentials_are_stripped(tmp_path):
    out = tmp_path / "reports"
    roots = []
    for d in ("one", "two"):
        root = make_project(tmp_path / d, "clone")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text('[remote "origin"]\n\turl = https://user:ghp_secrettoken123@github.com/o/r.git\n')
        roots.append(root)
        analyze(root, out)
    assert len(list(out.iterdir())) == 1  # two checkouts of one remote share a workspace
    for f in out.rglob("*"):
        if f.is_file():
            assert "ghp_secrettoken123" not in f.read_text(), f.name
    assert json.loads((out / "clone" / "project.json").read_text())["remote"] == "github.com/o/r"


@pytest.mark.parametrize("url,expected", [
    ("git@github.com:owner/repo.git", "github.com/owner/repo"),
    ("https://token:x-oauth@gitlab.com/g/p", "gitlab.com/g/p"),
    ("ssh://git@host.example:2222/team/repo.git", "host.example/team/repo"),
    ("not a url", None), ("", None),
])
def test_sanitize_remote(url, expected):
    assert sanitize_remote(url) == expected


@pytest.mark.parametrize("name,expected", [
    ("My Project", "my-project"), ("", "project"), ("...", "project"), ("CON", "p-con"), ("a/b\\c", "a-b-c"),
    ("../../etc", "etc"), ("Ünïcode Näme", "unicode-name"), ("  -x- ", "x"), ("x" * 200, "x" * 60),
    ("<b>bold</b>&\"'", "b-bold-b"),
])
def test_slugify_is_safe(name, expected):
    assert slugify(name) == expected
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slugify(name))


def test_unusual_project_folder_names_work(tmp_path):
    root = make_project(tmp_path, '..weird <b>name</b> & "quotes" é')
    out = tmp_path / "reports"
    analyze(root, out)
    (ws,) = list(out.iterdir())
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*", ws.name) and ws.resolve().parent == out.resolve()
    html = (run_dir(out, root) / "report.html").read_text()
    assert "<b>name</b>" not in html and "&lt;b&gt;name&lt;/b&gt;" in html


def test_incomplete_runs_are_ignored(tmp_path):
    root = make_project(tmp_path)
    out = tmp_path / "reports"
    analyze(root, out)
    ws = find_workspace(out, root)
    broken_id, broken = new_run_dir(ws)  # crashed run: no metadata.json yet
    (broken / "findings.json").write_text("{}")
    assert broken_id not in {m["run_id"] for m in list_runs(ws)}
    assert latest_run(ws)["run_id"] != broken_id


# ---------------------------------------------------------------- one source of truth
class Tags(HTMLParser):
    VOID = frozenset({"meta", "br", "img", "input", "link", "hr"})

    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.ids, self.dups, self.scripts, self.external = [], [], set(), [], 0, []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            if d["id"] in self.ids:
                self.dups.append(d["id"])
            self.ids.add(d["id"])
        if tag == "script":
            self.scripts += 1
        for k in ("src", "href"):
            if str(d.get(k, "")).startswith(("http://", "https://")):
                self.external.append(d[k])
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(tag)
        else:
            self.stack.pop()


def parse(html):
    p = Tags()
    p.feed(html)
    return p


def test_markdown_and_html_contain_the_same_findings(sample_repo, tmp_path):
    out = tmp_path / "reports"
    analyze(sample_repo, out)
    rd = run_dir(out, sample_repo)
    data, meta = report_data(out, sample_repo), json.loads((rd / "metadata.json").read_text())
    md, html = (rd / "report.md").read_text(), (rd / "report.html").read_text()
    findings = data["result"]["findings"]
    assert findings
    for f in findings:
        title = next(t["title"] for t in data["finding_text"] if t["id"] == f["id"])
        for text in (md, html):
            assert f["id"] in text and title.replace("`", "") in re.sub(r"[`]", "", text.replace("&#x27;", "'"))
    counts = data["result"]["findings"]
    sev = {k: len([f for f in counts if f["severity"] == k]) for k in ("critical", "high", "medium", "low")}
    assert {k: meta["counts"][k] for k in sev} == sev
    assert f"| {sev['critical']} | {sev['high']} | {sev['medium']} | {sev['low']} |" in md
    for label, n in (("Critical", sev["critical"]), ("High", sev["high"])):
        assert f'<div class="n">{n}</div><div class="l">{label}</div>' in html
    # the same executive summary text in both
    assert data["summary"]["status_headline"] in md and data["summary"]["status_headline"] in html
    assert meta["health"] == data["summary"]["status_label"]


def test_html_is_valid_self_contained_and_pinned_by_csp(sample_repo, tmp_path):
    out = tmp_path / "reports"
    analyze(sample_repo, out)
    html = (run_dir(out, sample_repo) / "report.html").read_text()
    p = parse(html)
    assert p.errors == [] and p.stack == [] and p.dups == [] and p.external == []
    assert html.startswith("<!doctype html>") and '<html lang="en">' in html and "<main" in html and "<title>" in html
    assert "https://" not in re.sub(r'content="[^"]*"', "", html).replace("vscode://", "")  # works offline
    assert p.scripts == 1
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1).replace("&#x27;", "'")
    assert f"script-src 'sha256-{digest}'" in csp and "default-src 'none'" in csp and "connect-src" not in csp
    for needed in ('aria-label="Report sections"', 'class="skip"', "prefers-color-scheme: dark", "@media print", "<details>"):
        assert needed in html
    assert "<svg" in html and "<title id=" in html  # accessible charts


def test_repository_text_is_escaped_in_html_and_markdown(tmp_path):
    root = make_project(tmp_path, code='eval("<script>alert(1)</script>")  # <img src=x onerror=alert(1)>\n')
    out = tmp_path / "reports"
    analyze(root, out)
    rd = run_dir(out, root)
    html, md = (rd / "report.html").read_text(), (rd / "report.md").read_text()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x" not in html and parse(html).scripts == 1  # only our own script
    assert "onerror" in html  # shown as inert text, not dropped
    outside = re.sub(r"(`{3,}).*?\1", "", md, flags=re.DOTALL)
    assert "<script>" not in outside and "<img" not in outside


def test_no_secrets_or_paths_in_metadata_and_nothing_leaks_the_api_key(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-REALLYSECRETKEYVALUE1234567890")
    out = tmp_path / "reports"
    analyze(sample_repo, out)
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text()
            assert "REALLYSECRETKEY" not in text and not any(b in text for b in BAD), f.name
    meta_text = (run_dir(out, sample_repo) / "metadata.json").read_text()
    assert str(tmp_path) not in meta_text and str(tmp_path) not in (out / "sample" / "project.json").read_text()


# ---------------------------------------------------------------- content
def clean_project(tmp_path):
    root = make_project(tmp_path, "clean", "x = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_mod.py").write_text("import mod\n")
    (root / "README.md").write_text("# clean\n")
    return root


def test_empty_findings_produce_a_valid_friendly_report(tmp_path):
    root = clean_project(tmp_path)
    out = tmp_path / "reports"
    analyze(root, out)
    rd = run_dir(out, root)
    data, md, html = report_data(out, root), (rd / "report.md").read_text(), (rd / "report.html").read_text()
    assert data["result"]["findings"] == [] and data["summary"]["status_label"] == "Healthy"
    assert "No findings were reported" in md and "No findings were reported" in html
    assert "No urgent actions" in md and parse(html).errors == []
    assert not (rd / "assets" / "severity.svg").exists()  # no chart without data
    assert "![Findings by severity]" not in md and "<svg" not in html


def test_strengths_are_only_claimed_with_evidence(tmp_path):
    clean = report_data(tmp_path / "o1" if analyze(clean_project(tmp_path), tmp_path / "o1") else None, tmp_path / "clean")
    titles = {s["title"] for s in clean["strengths"]}
    assert "The project has automated tests" in titles and "There is a README" in titles
    bare = make_project(tmp_path, "bare")
    analyze(bare, tmp_path / "o2")
    claimed = {s["title"] for s in report_data(tmp_path / "o2", bare)["strengths"]}
    # a bare project has no tests, README or CI: none of those may be claimed (only the evidenced parse check)
    assert claimed <= {"Python files parse cleanly"}


def test_language_is_plain_and_context_is_given_for_fake_values(sample_repo, tmp_path):
    out = tmp_path / "reports"
    analyze(sample_repo, out)
    md = (run_dir(out, sample_repo) / "report.md").read_text()
    assert "A string that looks like an API key or token is in the code" in md  # plain title, not "hardcoded-secret"
    assert "What we found." in md and "Why it matters." in md and "What could happen." in md and "How to fix it." in md
    assert "A comment next to it calls this value \"fake\"" in md  # the fixture's own comment is surfaced
    assert "confirmed security incident" not in md.lower() and "has been compromised" not in md.lower()


def test_testing_section_never_claims_coverage(sample_repo, tmp_path):
    out = tmp_path / "reports"
    analyze(sample_repo, out)
    md = (run_dir(out, sample_repo) / "report.md").read_text()
    assert "not measured by Repo Doctor" in md and "% coverage" not in md.lower()


def test_methodology_is_transparent_about_skipped_tools(sample_repo, tmp_path):
    out = tmp_path / "reports"
    runner.invoke(app, ["quality", "--path", str(sample_repo), "--no-llm", "--out", str(out)])  # tools denied (no approval)
    data = report_data(out, sample_repo)
    states = {t["name"]: t["status"] for t in data["methodology"]["tools"]}
    assert "denied" in states.values() or "skipped" in states.values()
    md = (run_dir(out, sample_repo) / "report.md").read_text()
    assert "Limitations" in md and "No AI model was used" in md and "never claims" not in md


# ---------------------------------------------------------------- history dashboard
def test_dashboard_shows_history_and_trend_for_comparable_runs(tmp_path):
    root = make_project(tmp_path)
    out = tmp_path / "reports"
    analyze(root, out)
    assert "Trend" not in (out / "proj" / "index.html").read_text()  # one run: nothing to compare
    (root / "mod.py").write_text("try:\n    pass\nexcept:\n    pass\n")
    analyze(root, out)
    html = (out / "proj" / "index.html").read_text()
    assert "<h2>Trend</h2>" in html and 'aria-labelledby="trend-t trend-d"' in html
    assert html.count("report.html") == 2 and "first run" in html and parse(html).errors == []
    analyze(root, out, mode="security")  # different mode: listed, but not mixed into the trend
    html = (out / "proj" / "index.html").read_text()
    assert html.count("report.html") == 3 and "Security analysis" in html


def test_project_json_and_dashboard_are_rebuilt_from_runs_on_disk(tmp_path):
    root = make_project(tmp_path)
    out = tmp_path / "reports"
    analyze(root, out)
    analyze(root, out)
    pj = out / "proj" / "project.json"
    stale = json.loads(pj.read_text())
    stale["runs"], stale["run_count"] = [], 0  # a stale/lost run list must not lose history
    pj.write_text(json.dumps(stale))
    analyze(root, out)
    fresh = json.loads(pj.read_text())
    assert fresh["run_count"] == 3 and len(fresh["runs"]) == 3 and fresh["identity_hash"] == stale["identity_hash"]
    assert (out / "proj" / "index.html").read_text().count("report.html") == 3


# ---------------------------------------------------------------- compatibility
def test_fix_uses_the_latest_run_and_writes_its_report_there(tmp_path, monkeypatch):
    from fakes import FakeProvider
    code = "def load(path):\n    try:\n        return open(path).read()\n    except:\n        return None\n"
    root = make_project(tmp_path, code=code)
    out = tmp_path / "reports"
    analyze(root, out, mode="quality")
    first = run_dir(out, root)
    analyze(root, out, mode="quality")
    latest = run_dir(out, root)
    assert latest != first
    fid = next(f["id"] for f in report_data(out, root)["result"]["findings"] if f["title"] == "Bare except")
    prov = FakeProvider({"edits": [{"search": "    except:\n        return None",
                                    "replace": "    except OSError:\n        return None", "reason": "narrow"}]})
    monkeypatch.setattr("repo_doctor.cli.create_provider", lambda settings: prov)
    r = runner.invoke(app, ["fix", "--path", str(root), "--out", str(out), "--id", fid, "--no-tools"], input="y\n")
    assert r.exit_code == 0, r.output
    assert latest.name in r.output and list(latest.glob("fix-report-*.md")) and not list(first.glob("fix-report-*.md"))
    # an explicit older run can be chosen, and bad ids are rejected cleanly
    bad = runner.invoke(app, ["fix", "--path", str(root), "--out", str(out), "--run", "../../etc", "--list"])
    assert bad.exit_code == 1 and "invalid run id" in bad.output
    missing = runner.invoke(app, ["fix", "--path", str(root), "--out", str(out), "--run", "2020-01-01_000000_abcdef", "--list"])
    assert missing.exit_code == 1 and "not found" in missing.output


def test_exchange_folder_stays_at_the_reports_root_not_inside_a_project(tmp_path):
    root = make_project(tmp_path)
    out = tmp_path / "reports"
    r = runner.invoke(app, ["analyze", "--path", str(root), "--out", str(out), "--provider", "exchange", "--no-tools"])
    assert r.exit_code == 0, r.output
    assert (out / ".exchange").is_dir() and not (out / "proj" / ".exchange").exists()
    assert find_workspace(out, root).slug == "proj"  # the dot-folder is never mistaken for a project
    pending = json.loads(runner.invoke(app, ["exchange", "pending", "--out", str(out)]).output)["pending"]
    assert pending
