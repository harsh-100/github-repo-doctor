import tomllib
from pathlib import Path

import repo_doctor

ROOT = Path(__file__).parent.parent


def test_version_matches_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert repo_doctor.__version__ == data["project"]["version"]
    assert f"## {repo_doctor.__version__}" in (ROOT / "CHANGELOG.md").read_text()


def test_required_project_files_exist():
    for name in ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", ".gitignore", ".env.example",
                 "docs/ARCHITECTURE.md", "docs/INSTALLATION.md", "docs/CONFIGURATION.md", "docs/AGENTS.md",
                 ".github/workflows/ci.yml"):
        assert (ROOT / name).is_file(), name


def test_env_example_has_no_real_keys():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            assert line.split("=", 1)[1].strip() == ""


def test_example_reports_contain_no_secrets():
    for f in (ROOT / "examples" / "reports").glob("*"):
        text = f.read_text()
        for secret in ("hunter2secret", "sk-ant-abcdefghijklmnop", "abcdef123456"):
            assert secret not in text, f.name
