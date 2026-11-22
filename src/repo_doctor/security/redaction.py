"""Secret redaction. Everything bound for an LLM or a report goes through here."""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL
)
# Well-known token shapes.
TOKEN_PATTERNS = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
]
# key = "value" assignments with suspicious key names.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>[\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|
        access[_\-]?key|private[_\-]?key|auth|credential)[\w.\-]*)
    (?P<sep>["']?\s*[:=]\s*["']?)
    (?P<val>[^\s"'#,;)}\]]{4,})
    """
)
# user:password@host in URLs.
_URL_CREDS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^/\s:@]+):(?P<pw>[^/\s@]+)@")

_PLACEHOLDER_VALUES = {"none", "null", "true", "false", "changeme", "your_key_here"}
# Values that are code references (env lookups, attribute access), not literals.
_CODE_REF = re.compile(r"^(?:os\.|process\.env|env\b|getenv|config\b|settings\b|self\.|args\.)")


def redact(text: str) -> str:
    """Return text with secrets replaced by [REDACTED]. Idempotent."""
    text = _PRIVATE_KEY.sub(REDACTED, text)
    for pat in TOKEN_PATTERNS:
        text = pat.sub(REDACTED, text)
    text = _URL_CREDS.sub(lambda m: f"{m['scheme']}{m['user']}:{REDACTED}@", text)

    def _assign(m: re.Match[str]) -> str:
        val = m["val"]
        if (
            val.startswith(("[REDACTED", "$", "{", "<"))
            or val.lower() in _PLACEHOLDER_VALUES
            or _CODE_REF.match(val)
        ):
            return m.group(0)  # env references / templates are not secrets
        return f"{m['key']}{m['sep']}{REDACTED}"

    return _ASSIGNMENT.sub(_assign, text)


def contains_secret(text: str) -> bool:
    return redact(text) != text


def redact_lines(text: str) -> str:
    """Redact while preserving line count, so line numbers stay valid."""
    text = _PRIVATE_KEY.sub(lambda m: REDACTED + "\n" * m.group(0).count("\n"), text)
    return "\n".join(redact(line) for line in text.split("\n"))
