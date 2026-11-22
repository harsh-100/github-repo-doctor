# Changelog

## 0.3.0

- **Project report workspaces.** Reports now live in `reports/<project>/runs/<run-id>/` and are never
  overwritten: each run keeps `report.html`, `report.md`, `findings.json`, `metadata.json` and `assets/`
  (charts). Each project gets `index.html` (history, trend), `project.json` and `README.md`. Projects are
  identified by git remote (credentials stripped) or path, so same-named folders never collide.
- **New offline HTML report** (dashboard cards, charts, filterable collapsible findings, light/dark theme,
  print/PDF) and a rewritten Markdown report in plain language: summary, what is working well, why each
  finding matters, action plan, and a transparent methodology/limitations section.
- `findings.json` is now the single source of truth both reports are rendered from.
- `--out` is now the reports *root*. `fix` and `report` use the project's latest run (`--run` to choose);
  `report --json` prints file locations; fix reports are saved inside the run folder.
- Fixed: runs started in the same second could be mis-ordered.

## 0.2.0

- **Use your Claude subscription (no API key):** `repo-doctor claude-install` adds `/doctor-analyze`,
  `/doctor-fix` and `/doctor-explain` slash commands (and a restricted `doctor-responder` subagent) to a
  project's `.claude/` folder. Claude Code answers Repo Doctor's model requests through a new file-based
  `exchange` provider (`--provider exchange`), so redaction, validation, reviewer and fix safeguards still apply.
- New commands: `fix-apply` (applies only a change whose Diff ID a human approved), `fix --list`,
  `exchange pending|show|answer|clear`, `claude-install`.
- `fix` can no longer pre-approve changes; only `fix-apply` can, and its Claude Code permission rule always asks.

## 0.1.0 (first public release)

- `setup`, `analyze`, `security`, `quality`, `tests`, `explain`, `report`, `fix` commands.
- Repository scanner (gitignore-aware, binary/secret-file/symlink safe) and project overview.
- Code, security and testing agents, reviewer agent, orchestrator, finding deduplication.
- Anthropic and OpenAI providers; secret redaction on everything sent to a model or written to a report.
- Ruff, Bandit, Gitleaks, ESLint, `tsc` and `npm audit` integration behind an approval policy.
- Approval-based `fix` workflow with backups, verification and automatic rollback.
