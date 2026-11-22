from __future__ import annotations

import httpx

from .base import LLMProvider, ProviderError


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    url = "https://api.anthropic.com/v1/messages"

    def _complete(self, system: str, prompt: str) -> str:
        try:
            resp = httpx.post(
                self.url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Anthropic API: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ProviderError(self._error_message(resp))
        try:
            return "".join(b["text"] for b in resp.json()["content"] if b.get("type") == "text")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Unexpected response format from Anthropic API") from exc
