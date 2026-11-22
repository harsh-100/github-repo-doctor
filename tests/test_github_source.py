"""GitHub source: URL validation, accessibility, hardened shallow clone, timeout/size limits, cleanup."""
import os
import subprocess
from pathlib import Path

import httpx
import pytest

from repo_doctor.core.settings import GitHubSettings
from repo_doctor.sources import github as gh
from repo_doctor.sources.github import (
    GitHubRef,
    RepoMeta,
    SourceError,
    clone_repository,
    cloned_repository,
    fetch_repo_meta,
    head_commit,
    parse_github_url,
)


# ---------------------------------------------------------------- URL validation
@pytest.mark.parametrize("url,owner,repo,branch", [
    ("https://github.com/octocat/Hello-World", "octocat", "Hello-World", None),
    ("https://github.com/octocat/Hello-World.git", "octocat", "Hello-World", None),
    ("https://github.com/octocat/Hello-World/", "octocat", "Hello-World", None),
    ("  https://github.com/octocat/Hello-World  ", "octocat", "Hello-World", None),
    ("https://www.github.com/octocat/hello.world", "octocat", "hello.world", None),
    ("https://github.com/octocat/Hello-World/tree/main", "octocat", "Hello-World", "main"),
    ("https://github.com/octocat/Hello-World/tree/feature/x-1", "octocat", "Hello-World", "feature/x-1"),
    ("https://GitHub.com/a/b_c", "a", "b_c", None),
])
def test_valid_urls(url, owner, repo, branch):
    ref = parse_github_url(url)
    assert (ref.owner, ref.repo, ref.branch) == (owner, repo, branch)
    assert ref.url == f"https://github.com/{owner}/{repo}" and ref.full_name == f"{owner}/{repo}"


@pytest.mark.parametrize("url,message", [
    ("", "No repository URL"),
    ("   ", "No repository URL"),
    ("git@github.com:octocat/Hello-World.git", "Only HTTPS"),
    ("ssh://git@github.com/octocat/Hello-World", "Only HTTPS"),
    ("git://github.com/octocat/Hello-World", "Only HTTPS"),
    ("http://github.com/octocat/Hello-World", "Only HTTPS"),
    ("file:///etc/passwd", "Only HTTPS"),
    ("/home/me/project", "Only HTTPS"),
    ("github.com/octocat/Hello-World", "include the scheme"),
    ("octocat/Hello-World", "does not look like a GitHub URL"),
    ("https://gitlab.com/octocat/Hello-World", "Unsupported host"),
    ("https://github.com.evil.example/octocat/Hello-World", "Unsupported host"),
    ("https://github.com:8443/octocat/Hello-World", "Unsupported host"),
    ("https://user:ghp_secret@github.com/octocat/Hello-World", "credentials"),
    ("https://token@github.com/octocat/Hello-World", "credentials"),
    ("https://github.com/octocat", "must point to a repository"),
    ("https://github.com/", "must point to a repository"),
    ("https://github.com/octocat/Hello-World/issues", "repository root URL"),
    ("https://github.com/octocat/Hello-World/blob/main/a.py", "repository root URL"),
    ("https://github.com/octocat/Hello-World?tab=readme", "query string"),
    ("https://github.com/octocat/Hello-World#readme", "query string"),
    ("https://github.com/-bad/repo", "not a valid GitHub username"),
    ("https://github.com/bad-/repo", "not a valid GitHub username"),
    ("https://github.com/octocat/--upload-pack=evil", "not a valid repository name"),
    ("https://github.com/octocat/..", "not a valid repository name"),
    ("https://github.com/octocat/re po", "not a valid"),
    ("https://github.com/octocat/Hello-World/tree/../../etc", "branch"),
    ("https://github.com/octocat/Hello-World/tree/-evil", "branch"),
])
def test_invalid_urls_are_rejected_with_a_clear_message(url, message):
    with pytest.raises(SourceError, match=message):
        parse_github_url(url)


# ---------------------------------------------------------------- accessibility (mocked GitHub API)
def fake_get(status=200, body=None, headers=None, exc=None):
    def _get(url, **kw):
        if exc:
            raise exc
        return httpx.Response(status, json=body if body is not None else {}, headers=headers or {})
    return _get


REF = GitHubRef("octocat", "Hello-World")
PUBLIC = {"full_name": "octocat/Hello-World", "private": False, "default_branch": "master", "description": "My first repo",
          "size": 1234, "stargazers_count": 7, "license": {"spdx_id": "MIT"}, "pushed_at": "2026-11-01T00:00:00Z",
          "archived": False, "fork": False, "language": "Python", "topics": ["demo"]}


def test_public_repository_metadata(monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get(200, PUBLIC))
    meta = fetch_repo_meta(REF, 5)
    assert (meta.full_name, meta.default_branch, meta.license, meta.stars, meta.topics) == (
        "octocat/Hello-World", "master", "MIT", 7, ["demo"]) and meta.api_available


def test_missing_or_private_repository(monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get(404))
    with pytest.raises(SourceError, match="not found, or it is private"):
        fetch_repo_meta(REF, 5)
    monkeypatch.setattr(httpx, "get", fake_get(200, {**PUBLIC, "private": True}))
    with pytest.raises(SourceError, match="private"):
        fetch_repo_meta(REF, 5)


def test_rate_limit_signals_fallback_but_other_403_is_an_error(monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get(403, headers={"x-ratelimit-remaining": "0"}))
    assert fetch_repo_meta(REF, 5).api_available is False
    monkeypatch.setattr(httpx, "get", fake_get(429))
    assert fetch_repo_meta(REF, 5).api_available is False
    monkeypatch.setattr(httpx, "get", fake_get(403, headers={"x-ratelimit-remaining": "42"}))
    with pytest.raises(SourceError, match="refused"):
        fetch_repo_meta(REF, 5)


@pytest.mark.parametrize("exc", [httpx.ConnectError("no route"), httpx.ReadTimeout("slow"), httpx.TransportError("x")])
def test_network_failures_are_friendly(monkeypatch, exc):
    monkeypatch.setattr(httpx, "get", fake_get(exc=exc))
    with pytest.raises(SourceError, match="Could not reach GitHub"):
        fetch_repo_meta(REF, 5)


def test_server_errors_and_garbage(monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get(503))
    with pytest.raises(SourceError, match="HTTP 503"):
        fetch_repo_meta(REF, 5)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(SourceError, match="unexpected response"):
        fetch_repo_meta(REF, 5)


def test_no_token_is_ever_sent(monkeypatch):
    seen = {}
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_shouldnotbeused")

    def _get(url, **kw):
        seen.update(kw)
        return httpx.Response(200, json=PUBLIC)

    monkeypatch.setattr(httpx, "get", _get)
    fetch_repo_meta(REF, 5)
    assert "authorization" not in {k.lower() for k in seen["headers"]}


# ---------------------------------------------------------------- real git, local repo through the exact same flags
@pytest.fixture
def origin(tmp_path):
    src = tmp_path / "origin"
    src.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (src / "a.py").write_text("print(1)\n")
    run("add", ".")
    run("commit", "-qm", "one")
    (src / "a.py").write_text("print(2)\n")
    run("commit", "-qam", "two")
    (src / "docs").mkdir()
    (src / "docs" / "x.md").write_text("x")
    if os.name != "nt":
        (src / "link").symlink_to("/etc/passwd")
    run("add", ".")
    run("commit", "-qm", "three")
    return src


def test_real_shallow_clone_is_hardened(origin, tmp_path):
    dest = tmp_path / "clone"
    clone_repository(REF, dest, GitHubSettings(), transport="file", url=origin.as_uri())
    count = subprocess.run(["git", "-C", str(dest), "rev-list", "--count", "HEAD"], check=False, capture_output=True, text=True).stdout.strip()
    assert count == "1"  # shallow: no history downloaded
    assert (dest / "a.py").read_text() == "print(2)\n"
    if os.name != "nt":
        assert not (dest / "link").is_symlink()  # symlinks are never checked out as links
    assert head_commit(dest) and len(head_commit(dest)) == 40


def test_existing_directory_is_never_overwritten(origin, tmp_path):
    dest = tmp_path / "mine"
    dest.mkdir()
    (dest / "precious.txt").write_text("keep me")
    with pytest.raises(SourceError, match="existing directory"):
        clone_repository(REF, dest, GitHubSettings(), transport="file", url=origin.as_uri())
    assert (dest / "precious.txt").read_text() == "keep me"


def test_clone_failure_is_reported_cleanly(tmp_path):
    with pytest.raises(SourceError, match="git could not clone"):
        clone_repository(REF, tmp_path / "c", GitHubSettings(), transport="file", url=(tmp_path / "nope").as_uri())


def test_missing_branch_message(origin, tmp_path):
    with pytest.raises(SourceError, match="Branch 'ghost' was not found"):
        clone_repository(GitHubRef("o", "r", "ghost"), tmp_path / "c", GitHubSettings(), transport="file", url=origin.as_uri())


def test_head_commit_is_none_for_non_repos(tmp_path):
    assert head_commit(tmp_path) is None


# ---------------------------------------------------------------- command line, env, timeout, size (fake process)
class FakeProc:
    def __init__(self, cmd, **kw):
        FakeProc.cmd, FakeProc.env, FakeProc.kw = cmd, kw.get("env"), kw
        self.returncode, self.killed = None, False
        self.stderr = type("S", (), {"read": lambda s: b""})()

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed, self.returncode = True, -9

    def wait(self, *a, **k):
        return self.returncode


def test_clone_command_line_and_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    class Done(FakeProc):
        def __init__(self, cmd, **kw):
            super().__init__(cmd, **kw)
            self.returncode = 0

    monkeypatch.setattr(subprocess, "Popen", Done)
    clone_repository(GitHubRef("octocat", "Hello-World", "main"), tmp_path / "c", GitHubSettings())
    cmd = FakeProc.cmd
    for flag in ("--depth", "--single-branch", "--no-tags", "--no-recurse-submodules", "core.symlinks=false", "credential.helper=",
                 "protocol.allow=never", "protocol.https.allow=always", "core.fsmonitor=false"):
        assert flag in cmd, flag
    assert cmd[cmd.index("--depth") + 1] == "1"
    dd = cmd.index("--")
    assert cmd[dd + 1] == "https://github.com/octocat/Hello-World" and cmd[dd + 2] == str(tmp_path / "c")
    assert cmd.index("--branch") < dd  # options come before `--`, so nothing can be taken for an option
    env = FakeProc.env
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert not {k for k in env if "KEY" in k or "TOKEN" in k} and FakeProc.kw["stdin"] == subprocess.DEVNULL


def test_clone_timeout_kills_git_and_explains(monkeypatch, tmp_path):
    procs = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: procs.append(FakeProc(cmd, **kw)) or procs[-1])
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(gh.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(gh.time, "sleep", lambda s: None)
    with pytest.raises(SourceError, match="longer than 5s"):
        clone_repository(REF, tmp_path / "c", GitHubSettings(clone_timeout_seconds=5))
    assert procs[0].killed


def test_clone_size_limit_kills_git(monkeypatch, tmp_path):
    procs = []
    dest = tmp_path / "c"

    def popen(cmd, **kw):
        dest.mkdir()
        procs.append(FakeProc(cmd, **kw))
        return procs[-1]

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(gh, "_dir_size_mb", lambda p: 999.0)
    monkeypatch.setattr(gh.time, "sleep", lambda s: None)
    with pytest.raises(SourceError, match="larger than 10 MB"):
        clone_repository(REF, dest, GitHubSettings(max_clone_mb=10))
    assert procs[0].killed


def test_git_missing_is_explained(monkeypatch, tmp_path):
    monkeypatch.setattr(gh.shutil, "which", lambda name: None)
    with pytest.raises(SourceError, match="git is required"):
        clone_repository(REF, tmp_path / "c", GitHubSettings())


# ---------------------------------------------------------------- temporary workspace cleanup
def fake_clone_into(monkeypatch, fail=False):
    def _clone(ref, dest, settings, **kw):
        dest.mkdir(parents=True)
        (dest / "f.txt").write_text("x")
        os.chmod(dest / "f.txt", 0o444)  # read-only, like git pack files
        if fail:
            raise SourceError("boom")
    monkeypatch.setattr(gh, "clone_repository", _clone)
    monkeypatch.setattr(gh, "head_commit", lambda p: "a" * 40)


def test_temp_clone_is_removed_after_success(monkeypatch):
    fake_clone_into(monkeypatch)
    with cloned_repository(REF, RepoMeta(full_name="o/r"), GitHubSettings()) as c:
        parent = c.path.parent
        assert c.path.exists() and parent.name.startswith("repo-doctor-") and c.commit == "a" * 40
    assert not parent.exists()


def test_temp_clone_is_removed_when_the_analysis_fails_or_is_interrupted(monkeypatch):
    fake_clone_into(monkeypatch)
    for exc in (RuntimeError("analysis crashed"), KeyboardInterrupt()):
        with pytest.raises(type(exc)), cloned_repository(REF, RepoMeta(full_name="o/r"), GitHubSettings()) as c:
            parent = c.path.parent
            raise exc
        assert not parent.exists()


def test_temp_clone_is_removed_when_cloning_fails(monkeypatch):
    created = []
    real_mkdtemp = gh.tempfile.mkdtemp
    monkeypatch.setattr(gh.tempfile, "mkdtemp", lambda **kw: created.append(real_mkdtemp(**kw)) or created[-1])
    fake_clone_into(monkeypatch, fail=True)
    with pytest.raises(SourceError, match="boom"), cloned_repository(REF, RepoMeta(full_name="o/r"), GitHubSettings()):
        pass
    assert created and not Path(created[0]).exists()
