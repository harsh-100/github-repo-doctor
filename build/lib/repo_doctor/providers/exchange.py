"""File-exchange provider: lets an interactive Claude Code session act as the model.

Instead of calling an API, `complete()` writes the (already redacted) request to
`<dir>/<key>.request.json` and raises `ExchangePending` until `<dir>/<key>.response.txt` exists.
The `/doctor-*` Claude Code slash commands answer pending requests with the user's logged-in
subscription and re-run the analysis; every answer still passes through the normal validation,
reviewer and fix safeguards. Keys are content hashes, so identical prompts reuse earlier answers.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .base import LLMProvider, ProviderError

MAX_RESPONSE_BYTES = 400_000
REQUEST_SUFFIX = ".request.json"
RESPONSE_SUFFIX = ".response.txt"


class ExchangePending(ProviderError):
    """No response has been supplied yet for this request."""

    def __init__(self, key: str):
        super().__init__(f"awaiting response (exchange request {key})")
        self.key = key


def request_key(system: str, prompt: str) -> str:
    return hashlib.sha256(f"{system}\n\x00\n{prompt}".encode()).hexdigest()[:16]


class ExchangeProvider(LLMProvider):
    name = "exchange"

    def __init__(self, directory: Path, model: str = "claude-code-session"):
        super().__init__(api_key="", model=model, temperature=0.0, max_tokens=0, timeout=0.0)
        self.directory = Path(directory)

    def _complete(self, system: str, prompt: str) -> str:
        key = request_key(system, prompt)
        response = self.directory / f"{key}{RESPONSE_SUFFIX}"
        if response.is_file():
            if response.stat().st_size > MAX_RESPONSE_BYTES:
                raise ProviderError(f"exchange response {key} is too large")
            return response.read_text(encoding="utf-8", errors="replace")
        request = self.directory / f"{key}{REQUEST_SUFFIX}"
        if not request.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
            body = {"key": key, "response_path": str(response.resolve()), "system": system, "prompt": prompt}
            fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(body, fh, indent=1)
            os.replace(tmp, request)
        raise ExchangePending(key)

    def check_connection(self) -> None:  # nothing to connect to
        return None


def pending_requests(directory: Path) -> list[dict]:
    """Requests that have no response yet."""
    out = []
    if not directory.is_dir():
        return out
    for req in sorted(directory.glob(f"*{REQUEST_SUFFIX}")):
        key = req.name[: -len(REQUEST_SUFFIX)]
        if not (directory / f"{key}{RESPONSE_SUFFIX}").exists():
            out.append({"key": key, "request_file": str(req.resolve()),
                        "response_file": str((directory / f"{key}{RESPONSE_SUFFIX}").resolve()),
                        "chars": req.stat().st_size})
    return out


def clear_exchange(directory: Path) -> int:
    n = 0
    if directory.is_dir():
        for f in list(directory.glob(f"*{REQUEST_SUFFIX}")) + list(directory.glob(f"*{RESPONSE_SUFFIX}")):
            f.unlink()
            n += 1
    return n
