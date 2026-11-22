"""Configuration handling.

Config lives in a per-user directory (never inside the analysed repo):
  config.toml       - non-secret settings
  credentials.toml  - optional API keys, written with 0600 permissions

Environment variables always take precedence over stored credentials.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

# The ONLY place default model names live. Users override via config or env.
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4.1",
}
API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
SUPPORTED_PROVIDERS = tuple(DEFAULT_MODELS)


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    model: str = ""
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=2048, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0)


class ScanSettings(BaseModel):
    max_file_size_kb: int = Field(default=256, gt=0)
    max_files: int = Field(default=5000, gt=0)
    extra_excludes: list[str] = Field(default_factory=list)


class AgentSettings(BaseModel):
    max_llm_calls_per_agent: int = Field(default=4, ge=0)
    batch_chars: int = Field(default=12000, ge=1000)


class ToolSettings(BaseModel):
    trusted: bool = False  # auto-approve tool execution (explicit opt-in)
    timeout_seconds: int = Field(default=120, gt=0)
    disabled: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)

    @property
    def effective_model(self) -> str:
        return (
            os.environ.get("REPO_DOCTOR_MODEL")
            or self.llm.model
            or DEFAULT_MODELS.get(self.llm.provider, "")
        )


def config_dir() -> Path:
    override = os.environ.get("REPO_DOCTOR_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "repo-doctor"


def config_path() -> Path:
    return config_dir() / "config.toml"


def credentials_path() -> Path:
    return config_dir() / "credentials.toml"


class ConfigError(Exception):
    pass


def load_settings() -> Settings:
    path = config_path()
    if not path.exists():
        return Settings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(data)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise ConfigError(f"Invalid config file {path}: {exc}") from exc


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_settings(settings: Settings) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    excludes = ", ".join(_toml_str(e) for e in settings.scan.extra_excludes)
    text = (
        "[llm]\n"
        f"provider = {_toml_str(settings.llm.provider)}\n"
        f"model = {_toml_str(settings.llm.model)}\n"
        f"temperature = {settings.llm.temperature}\n"
        f"max_output_tokens = {settings.llm.max_output_tokens}\n"
        f"timeout_seconds = {settings.llm.timeout_seconds}\n"
        "\n[scan]\n"
        f"max_file_size_kb = {settings.scan.max_file_size_kb}\n"
        f"max_files = {settings.scan.max_files}\n"
        f"extra_excludes = [{excludes}]\n"
        "\n[agents]\n"
        f"max_llm_calls_per_agent = {settings.agents.max_llm_calls_per_agent}\n"
        f"batch_chars = {settings.agents.batch_chars}\n"
        "\n[tools]\n"
        f"trusted = {str(settings.tools.trusted).lower()}\n"
        f"timeout_seconds = {settings.tools.timeout_seconds}\n"
        f"disabled = [{', '.join(_toml_str(t) for t in settings.tools.disabled)}]\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


def save_api_key(provider: str, key: str) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_credentials()
    existing[provider] = key
    body = "".join(f"{p} = {_toml_str(k)}\n" for p, k in existing.items())
    # Create with restrictive permissions from the start (no world-readable window).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # e.g. Windows
    return path


def _read_credentials() -> dict[str, str]:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        return {k: str(v) for k, v in tomllib.loads(path.read_text("utf-8")).items()}
    except tomllib.TOMLDecodeError:
        return {}


def get_api_key(provider: str) -> str | None:
    env = API_KEY_ENV.get(provider)
    if env and os.environ.get(env):
        return os.environ[env]
    return _read_credentials().get(provider) or None
