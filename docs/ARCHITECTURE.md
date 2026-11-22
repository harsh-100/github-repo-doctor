# Architecture

```
cli.py ──► Orchestrator ──► ToolSuite (external tools, approval-gated)
              │            ├─► CodeAgent / SecurityAgent / TestingAgent
              │            ├─► dedup ─► ReviewerAgent ─► explainer
              ▼
        reports/health.py ─► reports/*.md + findings.json
cli.py fix ─► core/fixer.py ─► core/verify.py (rollback on failure) ─► reports/fix_report.py
```

## Modules (`src/repo_doctor/`)

| Path | Role |
|---|---|
| `cli.py`, `main.py` | Typer commands and terminal output |
| `core/settings.py` | config + credentials loading; the only place default model names live |
| `core/models.py` | Pydantic models (`Finding`, `ProjectProfile`, `AnalysisResult`, ...) |
| `core/orchestrator.py`, `core/dedup.py`, `core/explainer.py` | run, merge, explain |
| `core/fixer.py`, `core/verify.py` | fix proposals, backups, apply/rollback, verification |
| `providers/` | `LLMProvider` abstraction, Anthropic and OpenAI over `httpx` |
| `analyzers/` | repository scanner, language/framework detection, manifests, tool specs and parsers |
| `agents/` | agent base + validation pipeline, code/security/testing/reviewer agents |
| `tools/` | `RepoReader` (inventory-confined, redacting file access), `command_runner` |
| `security/` | `redaction.py`, `permissions.py` (path confinement, allowlist, approval policy) |
| `reports/` | Markdown rendering |

## Safety design

1. **Read-only analysis.** Nothing writes to the analysed repo except `fix`.
2. **One door to files.** Agents read through `RepoReader`, which only serves inventory files, confines
   paths to the repo root, and returns redacted, line-preserving text. Deterministic scanners get raw
   lines locally but only redacted evidence leaves them.
3. **One door to commands.** `command_runner` accepts only allowlisted executables as argv lists (no shell),
   with a minimal environment and a timeout; `ApprovalPolicy` decides whether a command may run.
4. **Redaction twice.** Content is redacted when built into prompts and again in the provider layer.
5. **Evidence-first.** Findings are validated against the real files; tool findings keep the tool's own
   message as evidence and are never rewritten by the model.
6. **Fix safety.** Search/replace edits must match once inside the shown window; originals are backed up
   outside the repo; any failed check rolls back.

## Design decisions

- Custom sequential orchestrator instead of an agent framework (five steps do not need one).
- `httpx` for providers instead of vendor SDKs, to keep dependencies minimal and the abstraction thin.
- No database: reports and `findings.json` are plain files.
- Windows support is best-effort until verified in CI.
