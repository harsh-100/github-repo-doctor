# Changelog

## 0.1.0 (first public release)

- `setup`, `analyze`, `security`, `quality`, `tests`, `explain`, `report`, `fix` commands.
- Repository scanner (gitignore-aware, binary/secret-file/symlink safe) and project overview.
- Code, security and testing agents, reviewer agent, orchestrator, finding deduplication.
- Anthropic and OpenAI providers; secret redaction on everything sent to a model or written to a report.
- Ruff, Bandit, Gitleaks, ESLint, `tsc` and `npm audit` integration behind an approval policy.
- Approval-based `fix` workflow with backups, verification and automatic rollback.
