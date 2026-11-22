# Contributing

Thanks for helping! The project is small on purpose: please keep changes simple and tested.

## Setup

```bash
git clone https://github.com/USERNAME/github-repo-doctor.git
cd github-repo-doctor
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev,tools]"
pytest && ruff check src tests
```

Tests never call a real LLM or need an API key: use `FakeProvider` from `tests/fakes.py`.

## Ground rules

- **Read-only by default.** Only the `fix` command may change a user's files, and only after explicit
  approval. Never add a "yes to all" option.
- **Secrets never leave the machine unredacted.** Anything bound for a model or a report goes through
  `security/redaction.py`. Add a test when you touch this area.
- **No arbitrary command execution.** New external tools must be added to the allowlist in
  `security/permissions.py`, built from a fixed command (no shell), and go through `ApprovalPolicy`.
- **Evidence over claims.** Findings need a real file, line and evidence; model output must be validated
  (see `agents/base.py`).
- Don't hardcode model names outside `core/settings.py`.
- Add tests with your change, including failure paths. Don't claim something works if it isn't tested.

## Adding an agent

Subclass `Agent` in `src/repo_doctor/agents/`, set `name` and `commands`, return `Finding`s, and add it
to `default_agents()` in `core/orchestrator.py`. The orchestrator needs no other changes.

## Adding a tool

Add a parser in `analyzers/tool_parsers.py` and a spec in `analyzers/static_analysis.py` (applicability
check, command plan, install hint). Test the parser with realistic captured output.

## Pull requests

Describe what and why, list the tests you added, and note anything you could not test (for example
another OS or a tool you don't have installed).
