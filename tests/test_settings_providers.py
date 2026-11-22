import sys

import httpx
import pytest

from repo_doctor.core import settings as s
from repo_doctor.providers import ProviderError, create_provider
from repo_doctor.providers.anthropic import AnthropicProvider
from repo_doctor.providers.openai import OpenAIProvider


def test_defaults_when_no_config():
    st = s.load_settings()
    assert st.llm.provider == "anthropic" and st.effective_model == s.DEFAULT_MODELS["anthropic"]


def test_save_and_load_roundtrip(monkeypatch):
    st = s.Settings()
    st.llm.provider, st.llm.model = "openai", 'my"model'
    st.scan.extra_excludes = ["*.log"]
    s.save_settings(st)
    loaded = s.load_settings()
    assert loaded.llm.model == 'my"model' and loaded.scan.extra_excludes == ["*.log"]
    monkeypatch.setenv("REPO_DOCTOR_MODEL", "override")
    assert loaded.effective_model == "override"


def test_invalid_config_raises():
    s.config_path().parent.mkdir(parents=True)
    s.config_path().write_text("[llm]\ntemperature = 99\n")
    with pytest.raises(s.ConfigError):
        s.load_settings()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks/permissions")
def test_api_key_env_beats_stored(monkeypatch):
    path = s.save_api_key("anthropic", "stored-key")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert s.get_api_key("anthropic") == "stored-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert s.get_api_key("anthropic") == "env-key"


def test_create_provider_requires_key_and_known_name():
    with pytest.raises(ProviderError, match="No API key"):
        create_provider(s.Settings())
    st = s.Settings()
    st.llm.provider = "nope"
    with pytest.raises(ProviderError, match="Unknown provider"):
        create_provider(st)


def test_create_provider_ok(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    st = s.Settings()
    st.llm.provider = "openai"
    assert isinstance(create_provider(st), OpenAIProvider)


def _patch_post(monkeypatch, response=None, exc=None):
    sent = {}

    def fake_post(url, **kw):
        sent.update(kw)
        if exc:
            raise exc
        return response

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def _make(cls):
    return cls(api_key="k", model="m", temperature=0.1, max_tokens=50, timeout=5)


def test_anthropic_success_and_redacts_outgoing(monkeypatch):
    resp = httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})
    sent = _patch_post(monkeypatch, resp)
    assert _make(AnthropicProvider).complete("sys", "my key sk-ant-abcdefghijklmnop1234") == "hi"
    assert "sk-ant-abcdefghijklmnop1234" not in str(sent["json"])


def test_openai_success(monkeypatch):
    resp = httpx.Response(200, json={"choices": [{"message": {"content": "yo"}}]})
    _patch_post(monkeypatch, resp)
    assert _make(OpenAIProvider).complete("s", "p") == "yo"


@pytest.mark.parametrize("cls", [AnthropicProvider, OpenAIProvider])
def test_provider_http_error_and_network_error(monkeypatch, cls):
    _patch_post(monkeypatch, httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(ProviderError, match="401.*bad key"):
        _make(cls).complete("s", "p")
    _patch_post(monkeypatch, exc=httpx.ConnectError("boom"))
    with pytest.raises(ProviderError, match="Could not reach"):
        _make(cls).complete("s", "p")
    _patch_post(monkeypatch, httpx.Response(200, json={"weird": 1}))
    with pytest.raises(ProviderError, match="Unexpected response"):
        _make(cls).complete("s", "p")
