"""Run an allowlisted analysis tool safely: no shell, minimal env, timeout, bounded output."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from repo_doctor.security.permissions import validate_command

MAX_OUTPUT_BYTES = 20 * 1024 * 1024
_ENV_KEEP = ("PATH", "HOME", "USERPROFILE", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR",
             "APPDATA", "LOCALAPPDATA", "NODE_PATH", "VIRTUAL_ENV")


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


def safe_env() -> dict[str, str]:
    """Whitelisted environment: API keys and other secrets are NOT passed to tools."""
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env["NO_COLOR"] = "1"
    env["CI"] = "1"
    # No .pyc: stale bytecode (same size, same-second mtime) could make a verification run test OLD code,
    # and tools must not litter the analysed repository.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_command(argv: list[str], cwd: Path, timeout: int) -> CommandResult:
    validate_command(argv)
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=safe_env(), capture_output=True, timeout=timeout, shell=False, check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(argv, None, _dec(exc.stdout), _dec(exc.stderr), timed_out=True)
    out, err = proc.stdout, proc.stderr
    truncated = len(out) > MAX_OUTPUT_BYTES
    return CommandResult(argv, proc.returncode, _dec(out[:MAX_OUTPUT_BYTES]), _dec(err[:100_000]),
                         truncated=truncated)


def _dec(b: bytes | None) -> str:
    return (b or b"").decode("utf-8", errors="replace")
