import shutil
from pathlib import Path

import pytest

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


@pytest.fixture
def sample_repo(tmp_path):
    dest = tmp_path / "sample"
    shutil.copytree(SAMPLE, dest)
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
