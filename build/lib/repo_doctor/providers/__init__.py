from __future__ import annotations

from repo_doctor.core.settings import SUPPORTED_PROVIDERS, Settings, get_api_key

from .anthropic import AnthropicProvider
from .base import LLMProvider, ProviderError
from .openai import OpenAIProvider

_REGISTRY: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def create_provider(settings: Settings) -> LLMProvider:
    name = settings.llm.provider
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ProviderError(
            f"Unknown provider '{name}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    key = get_api_key(name)
    if not key:
        raise ProviderError(
            f"No API key for '{name}'. Set the environment variable or run `repo-doctor setup`."
        )
    return cls(
        api_key=key,
        model=settings.effective_model,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_output_tokens,
        timeout=settings.llm.timeout_seconds,
    )


__all__ = ["LLMProvider", "ProviderError", "create_provider"]
