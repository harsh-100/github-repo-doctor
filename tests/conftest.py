import shutil
from pathlib import Path

import pytest

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


@pytest.fixture
def sample_repo(tmp_path):
    """A copy of examples/sample-project. Its `.env` is created here, not committed: `.env` is gitignored on
    purpose (so it is absent from a fresh checkout such as CI) and a committed secrets file is bad practice."""
    dest = tmp_path / "sample"
    shutil.copytree(SAMPLE, dest)
    (dest / ".env").write_text("SECRET_TOKEN=abcdef123456\n")  # fake value; tests assert it is never read or reported
    return dest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_DOCTOR_HOME", str(tmp_path / "cfg"))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "REPO_DOCTOR_MODEL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def make_ctx():
    from repo_doctor.agents.base import AgentContext
    from repo_doctor.analyzers.project import build_profile
    from repo_doctor.analyzers.repository import scan_repository
    from repo_doctor.core.settings import Settings
    from repo_doctor.tools.file_reader import RepoReader

    def _make(root, provider=None, **agent_overrides):
        settings = Settings()
        for k, v in agent_overrides.items():
            setattr(settings.agents, k, v)
        inv = scan_repository(root)
        return AgentContext(root=root, profile=build_profile(root, inv), reader=RepoReader(root, inv),
                            settings=settings, provider=provider)
    return _make
