---
description: Fix Repo Doctor findings, one reviewed diff at a time, using your Claude Code login
argument-hint: [path] [F-001 F-003 ...]
allowed-tools: Bash(repo-doctor:*)
---

You are running Repo Doctor's fix workflow. The CLI validates every edit, backs up originals, applies only
what a human approved, verifies, and rolls back on failure. **You supply model answers via the
`doctor-responder` subagent and relay the human's decision. You never approve anything yourself.**
Do not read or edit the project's files yourself.

## Step 0: Parse `$ARGUMENTS`

A path (default `.`) and optional finding ids (like `F-003`). Findings come from the project's latest analysis run
(`--run <run-id>` picks an older one); if there is none, tell the user to run `/doctor-analyze` first and stop.

## Step 1: Choose findings

If no ids were given, run `repo-doctor fix --path <path> --list`, show the table, and ask the user which ids
to fix (AskUserQuestion, or free text). Only fixable findings are listed; secrets, dependency issues, and
missing tests are intentionally not auto-fixable.

## Step 2: For each chosen id, one at a time

1. Run `repo-doctor fix --path <path> --id <ID> --dry-run --provider exchange --no-tools`.
2. If requests are pending (`repo-doctor exchange pending`), launch `doctor-responder` for each key, wait, and
   repeat step 1 (at most 4 rounds).
3. When a diff is shown, present to the user: the finding title, file:line, the "Why" reason, the full diff,
   and its **Diff ID**. If instead the output says it was skipped (stale file, cannot fix, rejected edit),
   report that reason and move on.
4. Ask the user explicitly whether to apply **this** change (AskUserQuestion: Apply / Skip). Default is Skip.
5. Only if they chose Apply, run
   `repo-doctor fix-apply --path <path> --id <ID> --approve-diff <DIFF_ID> --provider exchange --no-tools`
   using exactly the Diff ID you showed them. Claude Code will ask them to confirm this command; that is
   expected. Never apply without both confirmations, never reuse a Diff ID for another change, and never run
   `fix-apply` for a change you did not show. Add `--run-tests` only if the user asked to verify with their
   tests (this runs their project's code; say so first).
6. Report the outcome: applied and verified, applied but unverified, or rolled back (and why), plus the
   backup directory the CLI printed.

## Step 3: Wrap up

Summarise what was applied, rolled back or skipped, point to the `fix-report-*.md` file the CLI printed (saved inside that analysis run's folder), remind the user to
review with `git diff` and re-run `/doctor-analyze`.
