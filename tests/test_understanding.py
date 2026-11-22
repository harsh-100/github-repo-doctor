"""Project Understanding: evidence-based, validated, honest about what it does not know."""
import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeProvider

from repo_doctor.analyzers import docs
from repo_doctor.analyzers.components import find_components
from repo_doctor.analyzers.technologies import detect_technologies
from repo_doctor.core.understanding import (
    build_evidence,
    deterministic_understanding,
    merge_ai,
    understand,
    valid_evidence,
    workflow_ascii,
)
from repo_doctor.providers import ExchangePending, ProviderError

README = """# Taskly

Taskly is a small command-line tool that keeps a to-do list in a text file, so you can manage tasks without leaving the terminal.

## Why Taskly?

Most to-do apps need an account and a browser. Taskly keeps everything in one local file you control.

## Who is this for?

- Developers who live in the terminal
- People who want plain-text task lists

## Features

- Add tasks with `taskly add`
- List and filter tasks with `taskly list`
- Sync tasks to the cloud with `taskly sync`

## Installation

Requires Python 3.11. You will also need an OPENAI_API_KEY for the smart-summary feature.

```bash
pip install taskly
export OPENAI_API_KEY=your-key
```

## Usage

```bash
taskly add "write report"
taskly list --done
```

## Roadmap

- Mobile app
- Team sharing

## Limitations

- Single user only.
"""
APP = '''import typer
import httpx
import yaml

app = typer.Typer()


@app.command()
def add(text: str):
    """Add a task to the list."""


@app.command("list")
def list_tasks():
    """List tasks."""
'''


@pytest.fixture
def taskly(tmp_path):
    root = tmp_path / "taskly"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "README.md").write_text(README)
    (root / "src" / "app.py").write_text(APP)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "taskly"\nversion = "0.2.0"\ndescription = "Terminal to-do list"\nrequires-python = ">=3.11"\n'
        'dependencies = ["typer", "httpx", "pyyaml", "requests-unused", "pydantic"]\n')
    (root / "tests" / "test_app.py").write_text("import pytest\nfrom src import app\n\ndef test_x():\n    assert app\n")
    return root


# ---------------------------------------------------------------- documentation parsing
def test_sections_ignore_headings_inside_code_blocks():
    md = "# A\ntext\n```bash\n# not a heading\nrun it\n```\n## B\n- one item here\n"
    titles = [s.title for s in docs.parse_sections(md)]
    assert titles == ["A", "B"]
    assert docs.parse_sections(md)[0].code_blocks[0][1] == ["# not a heading", "run it"]


def test_tagline_skips_badges_and_html_wrappers():
    md = ('<p align="center">\n  <img src="logo.svg" alt="x">\n</p>\n<p align="center">\n  &nbsp;\n</p>\n'
          '<p align="center">\n  A tiny framework for <a href="https://x.dev">testing</a> things.<br>\n  Fork it and own it.\n</p>\n')
    assert docs.tagline(md) == "A tiny framework for testing things. Fork it and own it."
    assert docs.tagline("# T\n\n![badge](x.svg)\n\n") is None


def test_commands_are_extracted_not_executed(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: called.append(a))
    sec = docs.parse_sections("## Install\n```bash\n$ pip install x\n# comment\nrm -rf /\nexport K=v\n```\n```json\n{\"a\": 1}\n```\n")[0]
    cmds = docs.commands_in(sec)
    assert "pip install x" in cmds and "export K=v" in cmds and not any(c.startswith("{") for c in cmds)
    assert called == []  # nothing from a README is ever run


# ---------------------------------------------------------------- components & technologies
def test_components_are_found_in_code(taskly, make_ctx):
    ctx = make_ctx(taskly)
    comps = {(c.kind, c.name) for c in find_components(ctx.profile, ctx.reader)}
    assert ("cli-command", "add") in comps and ("cli-command", "list") in comps


def test_claude_code_commands_agents_skills_are_components(tmp_path, make_ctx):
    root = tmp_path / "cc"
    for rel, text in {".claude/commands/rank.md": "# /rank - Triage jobs into a shortlist\n", ".claude/commands/apply.md":
                      "---\ndescription: Tailor a CV\n---\nbody", ".claude/agents/reviewer.md": "---\ndescription: Reviews drafts\n---\n",
                      ".claude/skills/scraper/SKILL.md": "# Scraper\nFinds jobs\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    ctx = make_ctx(root)
    by = {c.name: c for c in find_components(ctx.profile, ctx.reader)}
    assert by["/rank"].description == "Triage jobs into a shortlist" and by["/apply"].description == "Tailor a CV"
    assert by["reviewer"].kind == "claude-agent" and by["scraper"].kind == "claude-skill"


def test_technologies_distinguish_used_declared_only_and_example_files(taskly, make_ctx):
    ex = taskly / "examples" / "demo"
    ex.mkdir(parents=True)
    (ex / "package.json").write_text('{"dependencies": {"express": "^4", "react": "^18"}}')
    (ex / "server.js").write_text("const express = require('express');\n")
    ctx = make_ctx(taskly)
    techs = {t.name: t for t in detect_technologies(ctx.profile, ctx.reader)}
    assert techs["Typer"].confidence == "confirmed" and techs["httpx"].confidence == "confirmed"
    assert techs["pyyaml"].confidence == "confirmed"                                        # alias: pyyaml -> import yaml
    assert techs["requests-unused"].confidence == "declared-only"                          # declared, never imported
    assert "Express" not in techs and "React" not in techs                                  # manifests inside examples/ ignored
    assert techs["JavaScript"].confidence == "tests-only"                                   # only present in examples
    assert "Express" not in ctx.profile.frameworks and "React" not in ctx.profile.frameworks


def test_ai_integrations_need_real_usage(tmp_path, make_ctx):
    root = tmp_path / "ai"
    root.mkdir()
    (root / "requirements.txt").write_text("anthropic\nopenai\n")
    (root / "main.py").write_text("import anthropic\nclient = anthropic.Anthropic()\n")
    (root / "README.md").write_text("# AI\n\nUses Claude. Set ANTHROPIC_API_KEY.\n")
    ctx = make_ctx(root)
    techs = {t.name: t.confidence for t in detect_technologies(ctx.profile, ctx.reader)}
    assert techs["Anthropic API"] == "confirmed" and techs["OpenAI API"] == "declared-only"


# ---------------------------------------------------------------- deterministic understanding (no AI)
def test_understanding_from_readme_and_code_without_ai(taskly, make_ctx):
    u = understand(make_ctx(taskly), "taskly")
    assert u.what.startswith("Taskly is a small command-line tool") and u.what_source == "docs" and not u.ai_used
    assert "local file you control" in u.problem and u.problem_source == "docs"
    assert {a.basis for a in u.audience} == {"documented"} and len(u.audience) == 2
    gs = u.getting_started
    assert any("3.11" in x for x in gs.prerequisites) and "pip install taskly" in gs.install
    assert "taskly add \"write report\"" in gs.usage and any("OPENAI_API_KEY" in x for x in gs.paid_or_keys)
    assert gs.limitations == ["Single user only."] and any("README.md" in s for s in gs.sources)
    assert "No AI model was used" in " ".join(u.uncertainty)


def test_documented_but_unimplemented_features_are_distinguished(taskly, make_ctx):
    u = understand(make_ctx(taskly), "taskly")
    by = {f.text: f for f in u.features}
    assert by["Add tasks with taskly add"].status == "in-code" and "src/app.py" in by["Add tasks with taskly add"].evidence
    assert by["List and filter tasks with taskly list"].status == "in-code"
    assert by["Sync tasks to the cloud with taskly sync"].status == "documented"   # no `sync` command exists in the code
    assert {by["Mobile app"].status, by["Team sharing"].status} == {"planned"}      # roadmap items are plans, not features


def test_missing_readme_is_reported_honestly(tmp_path, make_ctx):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "tool.py").write_text("print('hi')\n" * 3)
    u = understand(make_ctx(root), "bare")
    assert u.what == "" and u.what_source == "none" and u.features == [] and u.audience == []
    joined = " ".join(u.uncertainty)
    assert "No README was found" in joined and "could not find a description" in joined


def test_empty_repository_does_not_crash(tmp_path, make_ctx):
    root = tmp_path / "empty"
    root.mkdir()
    u = understand(make_ctx(root), "empty")
    assert u.what == "" and u.technologies == [] and u.workflow == [] and u.maturity.level == "experimental"
    assert "very small" in " ".join(u.uncertainty)


def test_unsupported_technology_is_called_out(tmp_path, make_ctx):
    root = tmp_path / "rb"
    root.mkdir()
    (root / "app.rb").write_text("puts 'hi'\n")
    (root / "README.md").write_text("# Rb\n\nA tiny Ruby script that greets the world every morning.\n")
    u = understand(make_ctx(root), "rb")
    assert "No Python, JavaScript or TypeScript code was found" in " ".join(u.uncertainty)
    assert u.what.startswith("A tiny Ruby script")


def test_html_wrapped_readme_still_yields_a_description(tmp_path, make_ctx):
    root = tmp_path / "h"
    root.mkdir()
    (root / "README.md").write_text('<p align="center">\n  <img src="a.svg">\n</p>\n<p align="center">\n  &nbsp;\n</p>\n'
                                    '<p align="center">\n  An AI-powered helper built on <a href="x">Claude Code</a>.<br>\n  Fork it.\n</p>\n')
    assert "AI-powered helper built on Claude Code" in understand(make_ctx(root), "h").what


# ---------------------------------------------------------------- workflow
def test_workflow_only_from_an_order_the_docs_actually_give(tmp_path, make_ctx):
    root = tmp_path / "wf"
    for n in ("setup", "rank", "apply", "outcome"):
        (root / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
        (root / ".claude" / "commands" / f"{n}.md").write_text(f"# /{n} - do {n}\n")
    (root / "README.md").write_text("# WF\n\nIntro mentioning /apply first.\n\nThe route: /setup, then /rank, then /apply, then /outcome.\n")
    u = understand(make_ctx(root), "wf")
    assert [s.title for s in u.workflow] == ["/setup", "/rank", "/apply", "/outcome"] and u.workflow_source == "docs"
    assert workflow_ascii(u.workflow).splitlines()[:4] == ["/setup", "  |", "  v", "/rank"]
    (root / "README.md").write_text("# WF\n\nUse /apply. Later maybe /setup.\n\nAlso /rank somewhere else.\n")
    assert understand(make_ctx(root), "wf").workflow == []   # scattered mentions are not a workflow


def test_cli_commands_workflow_is_labelled_by_definition_order(taskly, make_ctx):
    u = understand(make_ctx(taskly), "taskly")
    assert u.workflow_source == "components" and [s.title for s in u.workflow] == ["add", "list"]


# ---------------------------------------------------------------- maturity
def _maturity(root, make_ctx, meta=None):
    ctx = make_ctx(root)
    ctx.source_meta = meta
    return deterministic_understanding(build_evidence(ctx), root.name).maturity


def test_maturity_is_cautious(taskly, tmp_path, make_ctx):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "a.py").write_text("x = 1\n")
    assert _maturity(bare, make_ctx).level == "experimental"
    m = _maturity(taskly, make_ctx)
    assert m.level in {"experimental", "early development"} and "no CI workflow found" in m.gaps
    assert "cannot be determined" in m.activity


def test_pre_one_point_zero_without_recent_activity_is_capped(taskly, make_ctx):
    (taskly / ".github" / "workflows").mkdir(parents=True)
    (taskly / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
    for f in ("CHANGELOG.md", "CONTRIBUTING.md", "LICENSE"):
        (taskly / f).write_text("x")
    (taskly / "docs").mkdir()
    (taskly / "docs" / "a.md").write_text("a")
    (taskly / "docs" / "b.md").write_text("b")
    assert _maturity(taskly, make_ctx).level == "early development"   # high score, but version 0.2.0 and activity unknown


def test_recent_activity_and_archive_status(taskly, make_ctx):
    (taskly / ".github" / "workflows").mkdir(parents=True)
    (taskly / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
    for f in ("CHANGELOG.md", "CONTRIBUTING.md", "LICENSE"):
        (taskly / f).write_text("x")
    recent = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    m = _maturity(taskly, make_ctx, {"pushed_at": recent})
    assert m.level == "actively developed" and "recent activity on GitHub" in m.positives and "5 day" in m.activity
    old = (datetime.now(UTC) - timedelta(days=900)).isoformat()
    assert "no pushes in over a year" in _maturity(taskly, make_ctx, {"pushed_at": old}).gaps
    m = _maturity(taskly, make_ctx, {"pushed_at": recent, "archived": True})
    assert "archived" in " ".join(m.gaps) and m.level != "mature-looking"


# ---------------------------------------------------------------- the AI step is validated
def ai_answer(**over):
    base = {"what": "Taskly keeps a to-do list in a text file you manage from the terminal.",
            "problem": "To-do apps usually need accounts.",
            "features": [{"text": "Add tasks", "evidence": ["src/app.py"]},
                         {"text": "Auto-prioritise with AI", "evidence": ["src/ai.py"]},          # invented file
                         {"text": "Works everywhere", "evidence": []},                            # no evidence
                         {"text": "Sync to the cloud", "evidence": ["README.md § Features"]},     # README only
                         {"text": "Mobile app is coming", "evidence": ["README.md § Roadmap", "README.md § Nope"]}],
            "audience": [{"text": "Developers", "quote": "Developers who live in the terminal"},
                         {"text": "Managers", "quote": "managers adore it"}],
            "workflow": [{"title": "Add", "detail": "add a task", "evidence": ["src/app.py"]},
                         {"title": "Teleport", "evidence": ["nowhere.py"]},
                         {"title": "List", "detail": "see tasks", "evidence": ["src/app.py"]}],
            "value": "One local file you control.", "uncertainty": ["Cloud sync is not shown in code."]}
    base.update(over)
    return base


def test_ai_answer_is_validated_against_the_evidence(taskly, make_ctx):
    p = FakeProvider(ai_answer())
    ctx = make_ctx(taskly, p)
    u = understand(ctx, "taskly")
    assert u.ai_used and u.what_source == "ai" and ctx.llm_calls == 1 and u.value == "One local file you control."
    by = {f.text: f for f in u.features}
    assert "Auto-prioritise with AI" not in by and "Works everywhere" not in by            # unsupported claims dropped
    assert by["Add tasks"].status == "in-code" and by["Sync to the cloud"].status == "documented"
    assert by["Mobile app is coming"].status == "planned" and by["Mobile app is coming"].evidence == ["README.md § Roadmap"]
    aud = {a.text: a.basis for a in u.audience}
    assert aud["Developers"] == "documented" and aud["Managers"] == "inferred"             # fake quote => inferred
    assert [s.title for s in u.workflow] == ["Add", "List"] and u.workflow_source == "ai"
    joined = " ".join(u.uncertainty)
    assert "Cloud sync is not shown in code" in joined and "2 AI-suggested feature(s) were left out" in joined
    assert "written by an AI model" in joined and "not run the project" in joined


def test_status_is_computed_by_repo_doctor_not_trusted_from_the_model(taskly, make_ctx):
    ev = build_evidence(make_ctx(taskly))
    det = deterministic_understanding(ev, "taskly")
    merged = merge_ai(det, ev, {"features": [{"text": "Everything", "status": "in-code", "evidence": ["README.md § Features"]}]})
    assert next(f for f in merged.features if f.text == "Everything").status == "documented"


def test_workflow_with_too_little_evidence_falls_back(taskly, make_ctx):
    u = understand(make_ctx(taskly, FakeProvider(ai_answer(workflow=[{"title": "Only", "evidence": ["src/app.py"]}]))), "taskly")
    assert u.workflow_source == "components"   # a single valid step is not a workflow; deterministic one is used


def test_evidence_reference_validation(taskly, make_ctx):
    ev = build_evidence(make_ctx(taskly))
    assert valid_evidence(ev, "src/app.py") and valid_evidence(ev, "README.md § Installation")
    assert valid_evidence(ev, "README.md § installation")
    assert not valid_evidence(ev, "src/ghost.py") and not valid_evidence(ev, "README.md § Ghost") and not valid_evidence(ev, "")


@pytest.mark.parametrize("failure,note", [
    (ProviderError("down"), "not available yet"),
    (ExchangePending("abc123"), "not available yet"),
    ("not json at all", "could not be read"),
])
def test_ai_failures_fall_back_to_documentation_with_a_note(taskly, make_ctx, failure, note):
    ctx = make_ctx(taskly, FakeProvider(failure))
    u = understand(ctx, "taskly")
    assert not u.ai_used and u.what.startswith("Taskly is a small command-line tool") and note in " ".join(u.uncertainty)
    assert ctx.warnings


def test_malformed_ai_fields_are_ignored(taskly, make_ctx):
    u = understand(make_ctx(taskly, FakeProvider({"what": 42, "features": "nope", "audience": [None, 3], "workflow": {"a": 1},
                                                   "uncertainty": "x"})), "taskly")
    assert u.ai_used and u.features and u.workflow_source == "components"


def test_no_model_call_without_anything_to_summarise(tmp_path, make_ctx):
    root = tmp_path / "e"
    root.mkdir()
    p = FakeProvider(ai_answer())
    u = understand(make_ctx(root, p), "e")
    assert p.prompts == [] and not u.ai_used and "Too little documentation" in " ".join(u.uncertainty)


def test_prompt_is_evidence_based_redacted_and_marks_untrusted_data(taskly, make_ctx):
    (taskly / "README.md").write_text(README + '\nTo test, set `password = "hunter2secret"` and key sk-ant-abcdefghijklmnop1234567890.\n')
    p = FakeProvider(ai_answer())
    understand(make_ctx(taskly, p), "taskly")
    prompt = p.prompts[0]
    assert "hunter2secret" not in prompt and "sk-ant-abcdefghijklmnop" not in prompt
    assert "COMPONENTS FOUND IN THE REPOSITORY" in prompt and "TECHNOLOGIES" in prompt and "cli-command add" in prompt
    assert "declared-only" in prompt and "untrusted data" in prompt   # the model is told what is weak evidence


def test_secrets_never_appear_in_the_understanding(taskly, make_ctx):
    (taskly / "README.md").write_text(README + '\n## Configuration\n\nSet `export API_TOKEN=ghp_' + "a" * 36 + '`.\n')
    leaked = ai_answer(what="Use sk-ant-abcdefghijklmnop1234567890 to log in.", value="password = \"hunter2secret\"")
    u = understand(make_ctx(taskly, FakeProvider(leaked)), "taskly")
    blob = u.model_dump_json()
    for secret in ("sk-ant-abcdefghijklmnop", "hunter2secret", "ghp_" + "a" * 36):
        assert secret not in blob


def test_understanding_roundtrips_as_json(taskly, make_ctx):
    u = understand(make_ctx(taskly), "taskly")
    from repo_doctor.core.models import ProjectUnderstanding
    assert ProjectUnderstanding.model_validate(json.loads(u.model_dump_json())).what == u.what


def test_ai_features_do_not_repeat_commands_they_already_cover(tmp_path, make_ctx):
    root = tmp_path / "cc"
    for n in ("rank", "apply", "reset"):
        (root / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
        (root / ".claude" / "commands" / f"{n}.md").write_text(f"# /{n} - do {n}\n")
    (root / "README.md").write_text("# CC\n\nA small toolkit of Claude Code commands for job searching and applications.\n")
    answer = {"what": "A toolkit of commands.", "features": [
        {"text": "Rank jobs", "evidence": [".claude/commands/rank.md"]},
        {"text": "Apply with a reviewed CV", "evidence": [".claude/commands/apply.md"]},
        {"text": "Another thing", "evidence": [".claude/commands/rank.md"]}]}
    u = understand(make_ctx(root, FakeProvider(answer)), "cc")
    texts = [f.text for f in u.features]
    assert texts[:3] == ["Rank jobs", "Apply with a reviewed CV", "Another thing"]
    assert len(texts) == 4 and texts[3].startswith("`/reset`")   # only the uncovered command is added
