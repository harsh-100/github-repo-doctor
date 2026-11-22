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


def test_example_report_workspace_is_complete_and_contains_no_local_paths():
    runs = list((ROOT / "examples" / "reports" / "sample-project" / "runs").iterdir())
    assert runs and {p.name for p in runs[0].iterdir()} >= {"report.html", "report.md", "findings.json", "metadata.json"}
    assert (ROOT / "examples" / "reports" / "sample-project" / "index.html").is_file()
    for f in (ROOT / "examples" / "reports").rglob("*"):
        if f.is_file():
            assert "/home/" not in f.read_text() and "/Users/" not in f.read_text(), f.name


def test_example_reports_contain_no_secrets():
    for f in (ROOT / "examples" / "reports").rglob("*"):
        if not f.is_file():
            continue
        text = f.read_text()
        for secret in ("hunter2secret", "sk-ant-abcdefghijklmnop", "abcdef123456"):
            assert secret not in text, f.name
