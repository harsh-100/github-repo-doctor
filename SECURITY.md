# Security Policy

## Reporting a vulnerability

Please report security issues privately (a GitHub security advisory on the repository, or email the
maintainers) rather than opening a public issue. Include steps to reproduce. We aim to acknowledge
reports within a few days.

## What Repo Doctor does with your code

- Analysis is read-only. Only `repo-doctor fix` modifies files, after you approve each change, and it
  backs up originals outside the repository first.
- Code excerpts are sent **only** to the LLM provider you configure, only when you have not passed
  `--no-llm`, and a notice is printed first. Secrets are redacted by pattern before sending; redaction is
  best-effort, so do not rely on it as the only protection for highly sensitive code (use `--no-llm`).
- `.env` files, private keys and similar are never read. Symlinks are not followed.
- External tools run only after approval (or with `--trust-tools`), from a fixed allowlist, without a
  shell, with a minimal environment that excludes your API keys.
- API keys come from environment variables or a `0600` file in your config directory, never from the repo.

## Scope notes

Tools such as ESLint can execute code from the project being analysed (config files, local binaries).
Only approve them for repositories you trust.
