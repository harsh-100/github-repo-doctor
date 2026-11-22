from __future__ import annotations

import httpx

from .base import LLMProvider, ProviderError


class OpenAIProvider(LLMProvider):
    name = "openai"
    url = "https://api.openai.com/v1/chat/completions"

    def _complete(self, system: str, prompt: str) -> str:
        try:
            resp = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": self.temperature,
                    "max_completion_tokens": self.max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach OpenAI API: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ProviderError(self._error_message(resp))
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("Unexpected response format from OpenAI API") from exc
