# Installation

Requirements: **Python 3.11+** and `git`. No Docker, database or cloud account is needed.
An AI provider API key is optional: without one, Repo Doctor still runs its built-in and tool checks.

## Install

```bash
git clone https://github.com/USERNAME/github-repo-doctor.git
cd github-repo-doctor
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .                   # add ".[tools]" to also install Ruff and Bandit
```

Check it works:

```bash
repo-doctor --version
repo-doctor analyze --path examples/sample-project --no-llm --no-tools
```

## Configure an AI provider (optional)

```bash
repo-doctor setup
```

`setup` checks your Python and dependencies, shows which analysis tools it found, asks for a provider
(`anthropic` or `openai`) and a model name, stores your key if you give one, and tests the connection.

An API key is **not** the same as a Claude or ChatGPT subscription. Create a key in the provider's
developer console and set it as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (recommended), or let `setup`
store it in a private file in your config directory.

## Optional analysis tools

Install any of these to get more findings; Repo Doctor never installs them for you.

| Tool | Install |
|---|---|
| Ruff, Bandit | `pip install ruff bandit` (or `pip install -e ".[tools]"`) |
| Gitleaks | https://github.com/gitleaks/gitleaks#installing |
| ESLint, TypeScript | `npm install --save-dev eslint typescript` inside the project you analyse |
| npm audit | Node.js and a `package-lock.json` |

## Troubleshooting

- `command not found: repo-doctor` — activate the virtualenv, or run `python -m repo_doctor.main`.
- `No API key` — export the key, or run with `--no-llm`.
- A tool shows `skipped` — the report says why (not installed, or not applicable to the project).
- A tool shows `denied` — you answered no, or there was no terminal; use `--trust-tools` to pre-approve.
