# Using Repo Doctor with your Claude subscription (no API key)

If you use Claude Code with a Pro/Max/Team login, you do not need an Anthropic API key. Repo Doctor adds
three slash commands to Claude Code: `/doctor-analyze`, `/doctor-fix` and `/doctor-explain`.

## Setup (once per project)

```bash
pip install -e .                       # Repo Doctor, in a virtualenv you keep active when running claude
cd /path/to/your/project
repo-doctor claude-install             # writes .claude/commands, .claude/agents, .claude/settings.json
claude                                 # logged in with your subscription; accept the workspace trust prompt
```

Then inside Claude Code:

```
/doctor-analyze .                      # full analysis; add: security | quality | tests
/doctor-analyze . --tools              # also run Ruff/Bandit/... (Claude Code asks you to confirm)
/doctor-fix .                          # list fixable findings, review one diff at a time
/doctor-explain .
```

You can also run them from the Repo Doctor folder itself (it ships its own `.claude/`): the first argument is
the path of the project to analyse.

## How it works

Repo Doctor does the scanning, tool runs, redaction, validation and reporting. Wherever it would call an AI
API it instead writes a **redacted request** to `reports/.exchange/` and the slash command asks Claude Code
(via the `doctor-responder` subagent) to answer it. Repo Doctor then re-runs and uses the answers exactly like
API answers: file paths and line numbers are checked, evidence must be found in the file, the reviewer still
runs, and fixes still need approval. Analysis takes a few rounds because answers unlock later steps.

You can see the exchange yourself: `repo-doctor exchange pending`, `repo-doctor exchange show KEY`.
Any run with `--provider exchange` (or `REPO_DOCTOR_PROVIDER=exchange`) works without Claude Code too, if you
want to answer requests by hand or from another tool: `repo-doctor exchange answer KEY < answer.txt`.

## Safety model

- **Redaction first.** Request files contain only redacted text; `.env` and key files are never read.
- **The responder is restricted.** It has only `Bash` (for `repo-doctor exchange show`) and `Write` (to create
  its answer file); no Read/Edit. The settings allow writes only to `reports/.exchange/`. Requests are treated
  as untrusted data and the agent is told never to follow instructions inside them.
- **Two human confirmations to change a file.** `/doctor-fix` shows you the diff and asks; applying then runs
  `repo-doctor fix-apply --approve-diff <Diff ID>`, whose permission rule always makes Claude Code ask you
  again. The Diff ID is a hash of the exact change, so an approval cannot be reused for a different edit, and
  the plain `fix` command has no way to self-approve.
- **Tools need approval.** `--tools` becomes `--trust-tools`, which also triggers a Claude Code confirmation.
- Deny rules block Claude from reading `.env`, `*.pem`, `*.key`, `~/.ssh`, `~/.aws` and Repo Doctor's own
  config directory.

## Things to know

- Usage counts against your Claude plan's limits. It uses the official Claude Code CLI with your own login;
  Repo Doctor never reads your credentials.
- If `ANTHROPIC_API_KEY` is set in your environment, Claude Code may use (and bill) it instead of your
  subscription. Unset it to use the subscription.
- The first time you run `claude` in a project you must accept the workspace trust prompt; until then Claude
  Code ignores the project's permission rules.
- The main Claude session still has its normal tools. The commands tell it to only run `repo-doctor ...`, and
  the deny rules protect secrets, but this is a policy layer, not a sandbox. Review prompts as usual.
- Tested with Claude Code 2.1.329 on Linux (headless `claude -p` runs of `/doctor-analyze` and `/doctor-fix`).
  Other versions and operating systems are untested. Only project-level install is provided.
- Without the exchange provider, the original API-key providers (`anthropic`, `openai`) still work as before.
