---
description: Analyze a repository with Repo Doctor using your Claude Code login (no API key)
argument-hint: [path or https://github.com/owner/repo] [security|quality|tests] [--tools]
allowed-tools: Bash(repo-doctor:*)
---

You are running Repo Doctor's analysis. Repo Doctor (the `repo-doctor` CLI) does all scanning, tool runs,
redaction, validation and reporting. **You only supply model answers** to the requests it writes, through the
`doctor-responder` subagent. Do not read the analysed project's files yourself, and do not run any command
except `repo-doctor ...`.

## Step 0: Parse `$ARGUMENTS`

- A local path (default `.`) **or a public GitHub repository URL** (`https://github.com/owner/repo`), and an optional mode: `security`, `quality` or `tests` (default: full `analyze`).
- `--tools` means the user wants external analysis tools (Ruff, Bandit, ...) to run. Without it, always pass
  `--no-tools`. Never add `--trust-tools` unless the user typed `--tools`; when they did, passing it makes
  Claude Code ask them to confirm the command, and that confirmation is their approval.
- If `repo-doctor` is not found, tell the user to activate the virtualenv where Repo Doctor is installed
  (or use `.venv/bin/repo-doctor`), then stop.

For a **GitHub URL**, first tell the user what will happen: Repo Doctor clones the repository (latest commit only)
into a temporary folder, analyses it without executing anything from it, saves the reports in their reports
folder, and deletes the clone. Then use `--repo <url> --yes` instead of `--path <path>` (the CLI prints the same
explanation; the user asked for this by typing the URL). Only HTTPS github.com URLs of public repositories work.

Define RUN as: `repo-doctor <mode> (--path <path> | --repo <url> --yes) --provider exchange <--no-tools | --trust-tools>`
(mode is `analyze` by default). Each round of a GitHub analysis clones again; that is expected.

## Step 1: Run and answer in rounds (at most 6 rounds)

1. Run RUN. It prints a provisional report and writes model requests to `reports/.exchange/`.
2. Run `repo-doctor exchange pending`. If `pending` is empty, go to Step 2.
3. For every pending item, launch the `doctor-responder` subagent with that item's `key` (up to 4 at once).
   Wait for all of them.
4. Go back to 1. (Answers unlock later requests: first the code/security/testing reviews, then the reviewer.)

If a round makes no progress (the same keys stay pending), stop and tell the user which keys failed.

## Step 2: Present the result

Run `repo-doctor report --path <path> --json` (or `--repo <url> --json` for a GitHub URL) to get the locations of this run's reports (`markdown`, `html`,
`dashboard`). Read the `markdown` file and summarise it in plain language: **what the project is, what problem it solves, what
users can do with it and how to get started (the "Understanding this project" section; say which parts are
documented by the project versus inferred)**, then project health, counts by severity,
the top findings (severity, file:line, one-line meaning), what is working well, which external tools ran,
skipped or were denied, and the recommended first action. Say clearly which findings are *Confirmed*
(tool/exact check) versus *Likely*/*Unconfirmed* (model claims).

Then tell the user where to look: the **HTML report** (open it in a browser, it works offline), the Markdown
report, and the **project dashboard** that keeps the history of every analysis (earlier reports are never
overwritten). Mention `/doctor-fix` for fixing, and that nothing in the project was modified.
