import json
import re
from importlib.resources import files
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repo_doctor.cli import app
from repo_doctor.core.claude_install import InstallError, install

runner = CliRunner()
SRC = Path(str(files("repo_doctor") / "claude_code"))
ROOT = Path(__file__).parent.parent


def frontmatter(path):
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(), re.DOTALL)
    assert m, f"{path.name} has no frontmatter"
    return dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)


def test_install_into_empty_project(tmp_path):
    actions = install(tmp_path)
    assert (tmp_path / ".claude/commands/doctor-analyze.md").is_file()
    assert (tmp_path / ".claude/agents/doctor-responder.md").is_file()
    perms = json.loads((tmp_path / ".claude/settings.json").read_text())["permissions"]
    assert "Bash(repo-doctor:*)" in perms["allow"] and "Bash(repo-doctor fix-apply:*)" in perms["ask"]
    assert any(a.startswith("wrote") for a in actions)


def test_install_is_idempotent(tmp_path):
    install(tmp_path)
    again = install(tmp_path)
    assert all(a.startswith("unchanged") for a in again)


def test_install_merges_existing_settings_and_keeps_backup(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(json.dumps({
        "model": "sonnet", "permissions": {"allow": ["Bash(git status)"], "deny": ["Read(./secrets/**)"]}}))
    install(tmp_path)
    data = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert data["model"] == "sonnet"                                   # unrelated keys kept
    assert "Bash(git status)" in data["permissions"]["allow"] and "Bash(repo-doctor:*)" in data["permissions"]["allow"]
    assert "Read(./secrets/**)" in data["permissions"]["deny"] and "Read(./.env)" in data["permissions"]["deny"]
    assert json.loads((tmp_path / ".claude/settings.json.bak").read_text())["model"] == "sonnet"
    install(tmp_path)
    again = json.loads((tmp_path / ".claude/settings.json").read_text())["permissions"]["allow"]
    assert len(again) == len(set(again))                               # no duplicates on re-run


def test_invalid_settings_json_is_never_touched(tmp_path):
    (tmp_path / ".claude").mkdir()
    bad = tmp_path / ".claude/settings.json"
    bad.write_text("{not json")
    with pytest.raises(InstallError, match="not valid JSON"):
        install(tmp_path)
    assert bad.read_text() == "{not json" and not (tmp_path / ".claude/commands").exists()


def test_modified_command_is_not_overwritten_without_force(tmp_path):
    install(tmp_path)
    cmd = tmp_path / ".claude/commands/doctor-fix.md"
    cmd.write_text("my customised version")
    assert any("SKIPPED" in a for a in install(tmp_path)) and cmd.read_text() == "my customised version"
    install(tmp_path, force=True)
    assert cmd.read_text() != "my customised version"


def test_cli_command(tmp_path):
    r = runner.invoke(app, ["claude-install", "--dir", str(tmp_path)])
    assert r.exit_code == 0 and "/doctor-analyze" in r.output
    bad = tmp_path / "x"
    (bad / ".claude").mkdir(parents=True)
    (bad / ".claude/settings.json").write_text("nope")
    assert runner.invoke(app, ["claude-install", "--dir", str(bad)]).exit_code == 1


# ---------------------------------------------------------------- the templates themselves
COMMANDS = sorted((SRC / "commands").glob("*.md"))


def test_templates_have_valid_frontmatter_and_minimal_tools():
    assert {c.stem for c in COMMANDS} == {"doctor-analyze", "doctor-fix", "doctor-explain"}
    for c in COMMANDS:
        fm = frontmatter(c)
        assert fm["description"].strip() and fm["allowed-tools"].strip() == "Bash(repo-doctor:*)"
    agent = frontmatter(SRC / "agents/doctor-responder.md")
    tools = {t.strip() for t in agent["tools"].split(",")}
    assert agent["name"].strip() == "doctor-responder" and tools == {"Bash", "Write"}  # no Read/Edit/Grep


def all_help():
    names = ["analyze", "security", "quality", "tests", "explain", "fix", "fix-apply", "report", "claude-install",
             "exchange pending", "exchange show", "exchange answer", "exchange clear"]
    return {n: runner.invoke(app, [*n.split(), "--help"], terminal_width=200).output for n in names}


def test_templates_only_reference_real_commands_and_flags():
    helps = all_help()
    text = "\n".join(p.read_text() for p in [*COMMANDS, SRC / "agents/doctor-responder.md"])
    for m in re.finditer(r"repo-doctor (exchange \w+|[a-z][a-z-]+)", text):
        sub = m.group(1)
        assert sub in helps or sub == "exchange", f"unknown command: {sub}"  # "exchange" alone = the group
    slash_only = {"--tools"}  # typed to /doctor-analyze itself, translated to --trust-tools/--no-tools
    for flag in set(re.findall(r"`?(--[a-z][a-z-]+)", text)) - slash_only:
        assert any(flag in h for h in helps.values()), f"flag not found in any --help: {flag}"


def test_permissions_fragment_is_safe():
    perms = json.loads((SRC / "settings.permissions.json").read_text())["permissions"]
    assert any("fix-apply" in r for r in perms["ask"])                         # human confirmation to apply
    assert any("--trust-tools" in r for r in perms["ask"])                     # human confirmation to run tools
    assert not any(r.startswith("Bash(repo-doctor fix-apply") for r in perms["allow"])
    for needed in ("Read(./.env)", "Read(~/.ssh/**)", "Read(~/.config/repo-doctor/**)"):
        assert needed in perms["deny"]
    assert "Edit(/reports/.exchange/**)" in perms["allow"]                     # Write only into the exchange folder
    assert not any(r in ("Bash", "Bash(*)", "Bash(:*)", "Edit", "Write", "Edit(**)") for r in perms["allow"])  # never a blanket Bash allow


def test_repo_root_dot_claude_matches_packaged_templates():
    """The repo ships its own .claude/ (generated by `claude-install`); keep it in sync."""
    for kind in ("commands", "agents"):
        for f in (SRC / kind).glob("*.md"):
            copy = ROOT / ".claude" / kind / f.name
            assert copy.is_file() and copy.read_text() == f.read_text(), f"{copy} is out of date: run claude-install --force"
