# Configuration

Config lives outside your repositories: `~/.config/repo-doctor/` (Linux/macOS) or
`%APPDATA%\repo-doctor\` (Windows). Override the directory with `REPO_DOCTOR_HOME`.

| File | Purpose |
|---|---|
| `config.toml` | settings (written by `repo-doctor setup`) |
| `credentials.toml` | optional stored API keys, permissions `0600` |
| `backups/` | originals saved by `repo-doctor fix` |

## config.toml

```toml
[llm]
provider = "anthropic"        # or "openai"
model = "MODEL_NAME"          # your choice; REPO_DOCTOR_MODEL overrides it
temperature = 0.1
max_output_tokens = 2048
timeout_seconds = 60

[scan]
max_file_size_kb = 256        # larger files are skipped
max_files = 5000              # inventory limit (the report notes truncation)
extra_excludes = ["*.log"]    # gitignore-style patterns, added to the defaults

[agents]
max_llm_calls_per_agent = 4   # cost control; 0 disables model calls for agents
batch_chars = 12000           # size of each code batch sent to the model

[tools]
trusted = false               # true = run analysis tools without asking
timeout_seconds = 120
disabled = []                 # e.g. ["eslint", "npm-audit"]
```

## Environment variables

| Variable | Meaning |
|---|---|
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | provider keys (take precedence over stored keys) |
| `REPO_DOCTOR_MODEL` | model name override |
| `REPO_DOCTOR_HOME` | config directory override |

## Default scan exclusions

Directories: `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`, `build`, `.next`,
`coverage`, `vendor`, `.cache`, `reports`, and common tool caches. Files: binaries, lockfiles, generated
(`*.min.js`, `*.map`), symlinks, anything in `.gitignore`, and secret-bearing files (`.env*`, `*.pem`,
`*.key`, `id_rsa`, ...). Secret files are never read; an un-gitignored one is reported by name only.

## Command flags worth knowing

`--no-llm` (no network), `--no-tools` (no external tools), `--trust-tools` (pre-approve tools),
`--out DIR` (report directory, default `./reports`), and for `fix`: `--id`, `--dry-run`, `--run-tests`.
