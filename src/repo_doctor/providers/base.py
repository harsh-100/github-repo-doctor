from __future__ import annotations

from abc import ABC, abstractmethod

from repo_doctor.security.redaction import redact


class ProviderError(Exception):
    """Raised for any provider failure (auth, network, bad response)."""


class LLMProvider(ABC):
    name: str

    def __init__(self, api_key: str, model: str, temperature: float, max_tokens: int, timeout: float):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, system: str, prompt: str) -> str:
        """Send a request. All outgoing text is redacted first (defence in depth)."""
        return self._complete(redact(system), redact(prompt))

    @abstractmethod
    def _complete(self, system: str, prompt: str) -> str: ...

    def check_connection(self) -> None:
        """Raise ProviderError if the provider is unreachable or the key/model is rejected."""
        saved = self.max_tokens
        self.max_tokens = 16
        try:
            self.complete("Reply with the single word: ok", "ping")
        finally:
            self.max_tokens = saved

    def _error_message(self, resp) -> str:
        try:
            body = resp.json()
            detail = body.get("error", {})
            detail = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            detail = resp.text[:200]
        return f"{self.name} API returned HTTP {resp.status_code}: {redact(str(detail))}"
