"""Path confinement and sensitive-file rules."""
from __future__ import annotations

from pathlib import Path


class PathSecurityError(Exception):
    pass


SENSITIVE_NAMES = {
    ".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx"}
# Template env files are safe and useful to read.
SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}


def is_sensitive_file(path: Path | str) -> bool:
    p = Path(path)
    name = p.name.lower()
    if name in SAFE_ENV_NAMES:
        return False
    if name in SENSITIVE_NAMES or name.startswith(".env."):
        return True
    return p.suffix.lower() in SENSITIVE_SUFFIXES


def resolve_repo_root(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if not root.exists():
        raise PathSecurityError(f"Path does not exist: {root}")
    if not root.is_dir():
        raise PathSecurityError(f"Path is not a directory: {root}")
    return root.resolve()


def safe_resolve(root: Path, relative: str | Path) -> Path:
    """Resolve `relative` under `root`; reject traversal and symlink escapes."""
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathSecurityError(f"Path escapes repository: {relative}")
    return candidate


# ---------------------------------------------------------------- command execution policy
from collections.abc import Callable
from dataclasses import dataclass

ALLOWED_EXECUTABLES = {"ruff", "bandit", "gitleaks", "eslint", "tsc", "npm", "pytest"}
ALLOWED_NPM_SUBCOMMANDS = {"audit", "test"}


class CommandNotAllowed(Exception):
    pass


def validate_command(argv: list[str]) -> None:
    """Only fixed, known analysis tools may run, as an argv list (never via a shell)."""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise CommandNotAllowed("empty or malformed command")
    name = Path(argv[0]).name.lower()
    for ext in (".exe", ".cmd", ".bat"):
        name = name.removesuffix(ext)
    if name not in ALLOWED_EXECUTABLES:
        raise CommandNotAllowed(f"executable '{name}' is not on the allowlist")
    if name == "npm" and (len(argv) < 2 or argv[1] not in ALLOWED_NPM_SUBCOMMANDS):
        raise CommandNotAllowed("only 'npm audit' and 'npm test' may be run")


@dataclass
class ApprovalPolicy:
    """Decides whether a tool command may run. Default: deny unless approved."""

    trusted: bool = False
    prompt: Callable[[str], bool] | None = None  # shown the full message; True = approve

    def approve(self, argv: list[str], purpose: str, network: bool = False, runs_repo_code: bool = False) -> bool:
        if self.trusted:
            return True
        if self.prompt is None:
            return False  # non-interactive and not trusted -> never run
        extras = []
        if network:
            extras.append("contacts the network (e.g. package registry)")
        if runs_repo_code:
            extras.append("executes code from this repository (project-local binary/config)")
        note = f"\n  Note: {'; '.join(extras)}." if extras else ""
        return bool(self.prompt(f"Run: {' '.join(argv)}\n  Purpose: {purpose}.{note}\nAllow?"))
