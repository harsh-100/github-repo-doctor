"""Read-only, inventory-confined file access. Output is always redacted, line-preserving."""
from __future__ import annotations

from pathlib import Path

from repo_doctor.core.models import Inventory
from repo_doctor.security.permissions import PathSecurityError, safe_resolve
from repo_doctor.security.redaction import redact_lines

MAX_LINE_CHARS = 400


class RepoReader:
    def __init__(self, root: Path, inventory: Inventory):
        self.root = root.resolve()
        self._paths = {f.path for f in inventory.files}
        self._cache: dict[str, str] = {}
        self._raw: dict[str, str] = {}

    def normalize(self, rel: str | None) -> str | None:
        """Map an agent-supplied path to an inventory path, or None if it isn't one."""
        if not rel or not isinstance(rel, str):
            return None
        rel = rel.strip().replace("\\", "/")
        if rel.startswith(str(self.root).replace("\\", "/") + "/"):
            rel = rel[len(str(self.root)) + 1 :]
        while rel.startswith("./"):
            rel = rel[2:]
        return rel if rel in self._paths else None

    def raw_lines(self, rel: str) -> list[str]:
        """UNREDACTED lines for local deterministic scanners ONLY. Never put these in a prompt
        or report; redact any evidence taken from them."""
        rel_n = self.normalize(rel)
        if rel_n is None:
            return []
        if rel_n not in self._raw:
            try:
                self._raw[rel_n] = safe_resolve(self.root, rel_n).read_text(encoding="utf-8", errors="ignore")
            except (OSError, PathSecurityError):
                self._raw[rel_n] = ""
        return self._raw[rel_n].split("\n") if self._raw[rel_n] else []

    def text(self, rel: str) -> str:
        rel_n = self.normalize(rel)
        if rel_n is None:
            return ""
        if rel_n not in self._cache:
            self._cache[rel_n] = redact_lines("\n".join(self.raw_lines(rel_n)))
        return self._cache[rel_n]

    def lines(self, rel: str) -> list[str]:
        return self.text(rel).split("\n") if self.text(rel) else []

    def chunks(self, rel: str, max_chars: int) -> list[tuple[int, int, str]]:
        """Split a file into (start_line, end_line, numbered_text) chunks."""
        out, buf, size, start = [], [], 0, 1
        for n, line in enumerate(self.lines(rel), 1):
            row = f"{n:>5}| {line[:MAX_LINE_CHARS]}"
            if buf and size + len(row) > max_chars:
                out.append((start, n - 1, "\n".join(buf)))
                buf, size, start = [], 0, n
            buf.append(row)
            size += len(row) + 1
        if buf:
            out.append((start, start + len(buf) - 1, "\n".join(buf)))
        return out
