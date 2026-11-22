# GitHub Repo Doctor

An AI-assisted CLI that scans a local repository and writes a Markdown report about it.
It runs entirely on your machine (no Docker, database or cloud service) and only talks to the
LLM provider you configure.

> **Status: v0.2.** Working: `setup`, `analyze`, `security`, `quality`, `tests`,
> `explain`, `report`, `fix`. Analysis never modifies your repository; only `fix` can, and only
> with your explicit approval of each change.

## Quickstart

```bash
git clone https://github.com/USERNAME/github-repo-doctor.git && cd github-repo-doctor
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[tools]"
repo-doctor analyze --path examples/sample-project --no-llm   # works with no API key
repo-doctor setup                                              # optional: connect an AI provider
repo-doctor analyze --path /path/to/your/project               # full analysis -> reports/
```

Docs: [Claude Code (subscription)](docs/CLAUDE_CODE.md) · [Installation](docs/INSTALLATION.md) · [Configuration](docs/CONFIGURATION.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Agents](docs/AGENTS.md) · [Example reports](examples/reports) ·
[Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [Changelog](CHANGELOG.md)

## No API key? Use your Claude subscription

Repo Doctor can run through Claude Code with your normal subscription login:

```bash
cd /path/to/your/project
repo-doctor claude-install     # adds /doctor-analyze, /doctor-fix, /doctor-explain to .claude/
claude                         # then type: /doctor-analyze .
```

Details, safety model and caveats: [docs/CLAUDE_CODE.md](docs/CLAUDE_CODE.md).

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/USERNAME/github-repo-doctor.git
cd github-repo-doctor
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Configure

```bash
repo-doctor setup
```

Checks your Python/dependencies, creates the config directory, asks for a provider
(`anthropic` or `openai`) and model, and tests the connection.

- **API key ≠ subscription.** A Claude or ChatGPT subscription does not include API access;
  create an API key in the provider's developer console.
- Provide the key via `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (preferred), or let `setup` store
  it in a `0600` file in the config directory (never inside a repo).
- Config: `~/.config/repo-doctor/config.toml` (`%APPDATA%\repo-doctor` on Windows; override with
  `REPO_DOCTOR_HOME`). Model names are never hard-coded in the app logic: set `model` in the
  config or `REPO_DOCTOR_MODEL`.

```toml
[llm]
provider = "anthropic"
model = "MODEL_NAME"
temperature = 0.1

[scan]
max_file_size_kb = 256
max_files = 5000
extra_excludes = ["*.log"]
```

## Use

```bash
repo-doctor analyze --path /path/to/project   # overview + all agents -> reports/REPO_HEALTH_REPORT.md
repo-doctor security --path .                 # security agent only   -> reports/SECURITY_REPORT.md
repo-doctor quality  --path .                 # code agent only       -> reports/QUALITY_REPORT.md
repo-doctor tests    --path .                 # testing agent only    -> reports/TESTS_REPORT.md
repo-doctor analyze --path . --no-llm         # deterministic checks only, no network
repo-doctor explain --path .                  # plain-language explanation
repo-doctor report                            # show the latest report
repo-doctor fix --path .                      # propose fixes, apply only what you approve
```

Every run also writes `reports/findings.json` (structured findings). Nothing in the analysed
repository is ever modified. Without an API key, agents still run their built-in checks.

## External analysis tools

Repo Doctor uses real tools when they are installed (it never installs anything itself):

| Tool | Area | Needs |
|---|---|---|
| Ruff | Python quality | `pip install ruff` |
| Bandit | Python security | `pip install bandit` |
| Gitleaks | secrets (always run with `--redact`) | [install gitleaks](https://github.com/gitleaks/gitleaks#installing) |
| ESLint | JS/TS quality | ESLint + a config in the project |
| TypeScript (`tsc --noEmit`) | TS type errors | TypeScript + `tsconfig.json` |
| `npm audit` | JS dependencies (contacts the npm registry) | Node.js + `package-lock.json` |

`pip install -e ".[tools]"` installs Ruff and Bandit. `repo-doctor setup` shows what is detected;
reports list every tool as `ran`, `skipped` (with the reason or install hint), `denied` or `failed`.

**Execution is permission-controlled.** Before each tool runs you see the exact command (with notes
if it uses the network or runs code from the repository, e.g. a project-local
`node_modules/.bin/eslint`) and must approve it. To skip prompts, opt in with `--trust-tools` or
`[tools] trusted = true` in the config. With no terminal and no trust, tools are **denied**.
`--no-tools` skips them entirely; `[tools] disabled = ["eslint"]` disables specific ones.
Only fixed, allowlisted commands run (no shell, no commands from the model or repository), with a
minimal environment (your API keys are not passed to tools) and a timeout.

Tool findings are `verified`, use the tool's own message as evidence, and are never rewritten by
the model; the LLM may only add a plain-language `AI explanation` (and a fix if the tool gave
none). A tool and a built-in check flagging the same line are merged, keeping the tool's result.

## Fixing findings (`repo-doctor fix`)

Run `analyze` (or `security` / `quality`) first; `fix` reads `reports/findings.json`.

```bash
repo-doctor fix --path .                 # pick findings interactively (numbers, ids or "all")
repo-doctor fix --id F-003 --id F-007    # specific findings
repo-doctor fix --dry-run                # show proposed diffs only, change nothing
repo-doctor fix --run-tests              # also run your tests to verify (asks first)
```

For each selected finding the model drafts a **minimal** change, which you see as a diff with the
reason for each edit; the file is only written if you answer yes. There is no "yes to all" flag, and
with no terminal input the answer is no.

Safeguards:

- The model returns search/replace edits, never whole files. Each `search` must match the real file
  **exactly once**, inside the code window the model was shown; otherwise the edit is rejected.
  The model only sees redacted code, edits touching `[REDACTED]` text are refused, and an edit that
  would add a secret-like value is refused. A change that breaks Python/JSON syntax is rejected
  before you are even asked.
- Findings are re-located in the current file; if the file changed since the analysis the fix is
  skipped (re-run `analyze`). If the file changes while waiting for your approval it is not applied.
- Originals are copied **outside the repository** (`<config dir>/backups/<repo>/<timestamp>/`)
  before the first change. Writes are atomic, keep file permissions, and never go through symlinks.
- After applying, checks run and **any failure rolls the change back automatically** (exit code 2):
  syntax; built-in detectors before/after (the targeted issue must be gone and no new issue may
  appear); the originating tool re-run (needs approval, same rules as analysis); and, with
  `--run-tests`, your project's tests (pytest or `npm test`; only counted as evidence if they passed
  *before* the change, otherwise reported as `inconclusive`). If nothing conclusive could be checked,
  the fix is reported as `applied (unverified)`.
- Not auto-fixable (kept in the report for manual action): hardcoded secrets (rotate the credential
  first), dependency vulnerabilities, missing tests, project-level and speculative findings.

`reports/FIX_REPORT.md` lists every outcome (applied, rolled back, declined, skipped) with diffs and
verification results. Review with `git diff` before committing, and re-run `analyze` afterwards.

## How it works

Agents run sequentially: **code**, **security**, **testing**, then the **reviewer**.

- Each agent runs deterministic checks first (Python AST checks, secret/unsafe-pattern scan,
  test-reference mapping), then optionally asks the LLM to review selected files in a bounded number
  of batches (`[agents] max_llm_calls_per_agent`, default 4). Reports state how much was covered.
- Model findings are validated: the file must be one that was actually sent, the line must be in
  the range sent, and the quoted evidence must be found in the file (the line is corrected to
  where it is found). Unlocated evidence => `unverified`, confidence capped, severity capped at medium.
- Verification levels: `verified` (deterministic check), `supported` (model claim, evidence found),
  `unverified`, `speculative` (reviewer), `rejected` (listed separately in the report).
- The reviewer can reject, downgrade or flag findings, and drop unreasonable fixes. It cannot add findings.
- Near-duplicate findings are merged, keeping the best-evidenced one.
- Secret findings never contain the secret value; `.env` files are reported by name only, never read.

Known limitations: `npm audit` and Gitleaks were verified against realistic captured output only
(not against live runs); Semgrep, `pip-audit` and executing the project's own tests are not
integrated yet; the built-in security checks are pattern-based (no data-flow analysis), so
"unsafe pattern" findings need human judgement; the testing agent infers coverage from references,
not from running tests.

## What gets sent to the LLM

A bounded summary (stack, directory counts, excerpts of README/manifests/entry points) and, for
agent review, line-numbered code excerpts of selected source files (capped per run; the CLI prints a
notice before sending). Everything is passed through secret redaction
(API-key shapes, private keys, `password=…` assignments, URL credentials) before sending, and
the provider layer redacts again as a safety net. `.env`, private keys and similar files are
never read. The scanner skips `.git`, `node_modules`, virtualenvs, build output, binaries,
lockfiles and anything in `.gitignore`, does not follow symlinks, and confines all reads to the
repository root. Analysis is read-only.

Redaction is pattern-based and best-effort; don't rely on it as the only protection for
highly sensitive code.

## Develop

```bash
pytest          # no network or API keys needed; LLM calls are mocked
ruff check src tests
```

## License

MIT. See [LICENSE](LICENSE).
# github-repo-doctor
