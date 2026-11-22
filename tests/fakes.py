"""Test doubles."""
import json as _json

from repo_doctor.security.redaction import redact


class FakeProvider:
    """Scripted LLM: replies are consumed in order (last one repeats). Records prompts."""

    name = "fake"
    model = "fake-model"

    def __init__(self, *replies):
        self.replies = [r if isinstance(r, (str, Exception)) else _json.dumps(r) for r in replies]
        self.prompts: list[str] = []

    def complete(self, system, prompt):
        self.prompts.append(redact(system) + "\n" + redact(prompt))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r


