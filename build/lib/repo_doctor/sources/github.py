"""Analyse a public GitHub repository: validate the URL, check it is public, shallow-clone it into an
isolated temporary directory, and always delete that directory afterwards.

Safety rules (each is covered by a test):
  * only https://github.com/<owner>/<repo> URLs; no credentials in the URL, no other hosts or schemes;
  * names are validated so nothing can be mistaken for a git option, and `--` precedes the URL;
  * the clone runs with no hooks, no symlink checkout, no credential helper, no prompts, no submodules,
    no user/system git config, and only the https transport;
  * a wall-clock timeout and a size cap kill the clone;
  * the destination is always a brand-new directory, so an existing checkout is never touched;
  * nothing from the repository is executed (no scripts, installs or builds).
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from repo_doctor.core.settings import GitHubSettings

OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
HOSTS = {"github.com", "www.github.com"}
API = "https://api.github.com/repos/{owner}/{repo}"


class SourceError(Exception):
    """A user-facing problem with the repository source (message is safe to print)."""


@dataclass(frozen=True)
class GitHubRef:
    owner: str
    repo: str
    branch: str | None = None

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class RepoMeta:
    full_name: str
    default_branch: str | None = None
    description: str | None = None
    size_kb: int | None = None
    stars: int | None = None
    license: str | None = None
    pushed_at: str | None = None
    archived: bool = False
    fork: bool = False
    language: str | None = None
    topics: list[str] | None = None
    api_available: bool = True


def parse_github_url(text: str) -> GitHubRef:
    """Strictly parse an HTTPS GitHub repository URL. Raises SourceError with a clear message."""
    raw = (text or "").strip()
    if not raw:
        raise SourceError("No repository URL given.")
    if re.match(r"^(?:git@|ssh://|git://)", raw) or raw.startswith(("http://", "file:", "/", ".", "~")):
        raise SourceError("Only HTTPS GitHub URLs are supported, for example https://github.com/owner/project")
    if "://" not in raw:
        if raw.split("/")[0].lower() in HOSTS:
            raise SourceError("Please include the scheme: https://github.com/owner/project")
        raise SourceError("That does not look like a GitHub URL. Use https://github.com/owner/project "
                          "(use --path for a local folder).")
    m = re.match(r"^https://([^/?#@]+@)?([^/?#:]+)(?::(\d+))?(/[^?#]*)?(\?[^#]*)?(#.*)?$", raw)
    if not m:
        raise SourceError("That is not a valid HTTPS URL.")
    userinfo, host, port, path, query, fragment = m.groups()
    if userinfo:
        raise SourceError("Do not put credentials or tokens in the URL. Public repositories need none.")
    if host.lower() not in HOSTS or port:
        raise SourceError(f"Unsupported host '{host}'. Only github.com repositories are supported.")
    if query or fragment:
        raise SourceError("Remove the query string or fragment; use the plain repository URL.")
    parts = [p for p in (path or "").split("/") if p]
    if len(parts) < 2:
        raise SourceError("The URL must point to a repository: https://github.com/owner/project")
    owner, repo, rest = parts[0], re.sub(r"\.git$", "", parts[1]), parts[2:]
    branch = None
    if rest:
        if rest[0] == "tree" and len(rest) >= 2:
            branch = "/".join(rest[1:])
        else:
            raise SourceError("Use the repository root URL (https://github.com/owner/project), "
                              "optionally with /tree/<branch>.")
    if not OWNER_RE.match(owner) or owner.endswith("-"):
        raise SourceError(f"'{owner}' is not a valid GitHub username or organisation name.")
    if not REPO_RE.match(repo) or repo in {".", ".."} or repo.startswith("-"):
        raise SourceError(f"'{repo}' is not a valid repository name.")
    if branch is not None and (not BRANCH_RE.match(branch) or ".." in branch or branch.endswith(("/", ".lock"))):
        raise SourceError("That branch name is not supported.")
    return GitHubRef(owner, repo, branch)


# ------------------------------------------------------------------ accessibility check
def fetch_repo_meta(ref: GitHubRef, timeout: float) -> RepoMeta:
    """Ask the GitHub API about the repository (no token). Raises SourceError for user-facing failures."""
    try:
        resp = httpx.get(API.format(owner=ref.owner, repo=ref.repo), timeout=timeout, follow_redirects=True,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "repo-doctor"})
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach GitHub ({type(exc).__name__}). Check your network connection.") from exc
    if resp.status_code == 404:
        raise SourceError(f"Repository {ref.full_name} was not found, or it is private. Only public repositories "
                          "are supported (private repositories are not supported yet).")
    if resp.status_code in (403, 429):
        if resp.headers.get("x-ratelimit-remaining") == "0" or resp.status_code == 429:
            return RepoMeta(full_name=ref.full_name, api_available=False)  # rate limited: caller falls back to git
        raise SourceError(f"GitHub refused the request for {ref.full_name} (HTTP 403). It may be unavailable "
                          "for legal reasons or restricted.")
    if resp.status_code != 200:
        raise SourceError(f"GitHub returned HTTP {resp.status_code} for {ref.full_name}. Try again later.")
    try:
        d = resp.json()
    except ValueError as exc:
        raise SourceError("GitHub returned an unexpected response.") from exc
    if d.get("private"):
        raise SourceError(f"{ref.full_name} is private. Only public repositories are supported.")
    lic = d.get("license") or {}
    return RepoMeta(
        full_name=d.get("full_name") or ref.full_name, default_branch=d.get("default_branch"),
        description=d.get("description"), size_kb=d.get("size"), stars=d.get("stargazers_count"),
        license=lic.get("spdx_id") or lic.get("name"), pushed_at=d.get("pushed_at"),
        archived=bool(d.get("archived")), fork=bool(d.get("fork")), language=d.get("language"),
        topics=d.get("topics") or [])


def _git_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR",
                                      "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY",
                                      "https_proxy", "NO_PROXY", "no_proxy") if k in os.environ}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_LFS_SKIP_SMUDGE": "1", "GCM_INTERACTIVE": "never"})
    return env


def _require_git() -> str:
    git = shutil.which("git")
    if not git:
        raise SourceError("git is required to analyse a GitHub repository but was not found on your PATH.")
    return git


def check_via_git(ref: GitHubRef, timeout: float) -> None:
    """Fallback accessibility check (used when the API is rate limited): `git ls-remote`."""
    cmd = [_require_git(), "-c", "credential.helper=", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
           "ls-remote", "--exit-code", "--", ref.url, "HEAD"]
    try:
        r = subprocess.run(cmd, env=_git_env(), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           check=False)
    except subprocess.TimeoutExpired as exc:
        raise SourceError("Timed out while contacting GitHub.") from exc
    if r.returncode != 0:
        raise SourceError(f"Repository {ref.full_name} was not found, is private, or GitHub is unreachable.")


# ------------------------------------------------------------------ clone
def _dir_size_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def _rmtree(path: Path) -> None:
    def onerror(func, p, _exc):  # read-only git pack files (notably on Windows)
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)


def clone_repository(ref: GitHubRef, dest: Path, settings: GitHubSettings, transport: str = "https",
                     url: str | None = None) -> None:
    """Shallow-clone into `dest` (must not exist). Raises SourceError on failure, timeout, or size overrun."""
    if dest.exists():
        raise SourceError("Refusing to clone into an existing directory.")
    cmd = [_require_git(), "-c", "credential.helper=", "-c", "core.symlinks=false", "-c", f"core.hooksPath={os.devnull}",
           "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", "-c", f"protocol.{transport}.allow=always",
           "clone", "--depth", "1", "--single-branch", "--no-tags", "--no-recurse-submodules", "--quiet"]
    if ref.branch:
        cmd += ["--branch", ref.branch]
    cmd += ["--", url or ref.url, str(dest)]
    proc = subprocess.Popen(cmd, env=_git_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    start = time.monotonic()
    try:
        while proc.poll() is None:
            if time.monotonic() - start > settings.clone_timeout_seconds:
                proc.kill()
                proc.wait()
                raise SourceError(f"Cloning took longer than {settings.clone_timeout_seconds}s and was stopped. "
                                  "Raise [github] clone_timeout_seconds in the config to allow more time.")
            if dest.exists() and _dir_size_mb(dest) > settings.max_clone_mb:
                proc.kill()
                proc.wait()
                raise SourceError(f"The repository is larger than {settings.max_clone_mb} MB and was not downloaded in "
                                  "full. Raise [github] max_clone_mb in the config to allow it.")
            time.sleep(0.25)
    finally:
        if proc.poll() is None:
            proc.kill()
    stderr = (proc.stderr.read().decode("utf-8", "replace") if proc.stderr else "").strip()
    if proc.returncode != 0:
        if "Remote branch" in stderr and "not found" in stderr:
            raise SourceError(f"Branch '{ref.branch}' was not found in {ref.full_name}.")
        raise SourceError(f"git could not clone {ref.full_name}: {stderr.splitlines()[-1][:200] if stderr else 'unknown error'}")
    if dest.exists() and _dir_size_mb(dest) > settings.max_clone_mb:
        raise SourceError(f"The repository is larger than {settings.max_clone_mb} MB. "
                          "Raise [github] max_clone_mb in the config to allow it.")


def head_commit(path: Path) -> str | None:
    """Commit id of the fresh clone (a plain rev-parse on a directory we just created)."""
    try:
        r = subprocess.run([_require_git(), "-C", str(path), "rev-parse", "HEAD"], env=_git_env(), capture_output=True,
                           timeout=15, stdin=subprocess.DEVNULL, check=False)
    except (subprocess.TimeoutExpired, SourceError, OSError):
        return None
    out = r.stdout.decode().strip()
    return out if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out) else None


@dataclass
class ClonedRepo:
    path: Path
    commit: str | None
    ref: GitHubRef
    meta: RepoMeta


@contextmanager
def cloned_repository(ref: GitHubRef, meta: RepoMeta, settings: GitHubSettings) -> Iterator[ClonedRepo]:
    """Clone into a fresh temp directory and ALWAYS remove it (success, failure or interruption)."""
    tmp = Path(tempfile.mkdtemp(prefix="repo-doctor-"))
    try:
        dest = tmp / re.sub(r"[^A-Za-z0-9._-]", "_", ref.repo)
        clone_repository(ref, dest, settings)
        yield ClonedRepo(dest, head_commit(dest), ref, meta)
    finally:
        _rmtree(tmp)
