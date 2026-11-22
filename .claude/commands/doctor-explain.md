---
description: Explain a project's architecture in plain language using your Claude Code login
argument-hint: [path]
allowed-tools: Bash(repo-doctor:*)
---

Explain the project at `$ARGUMENTS` (default `.`) using Repo Doctor. Only run `repo-doctor ...` commands; do
not read the project's files yourself.

1. Run `repo-doctor explain --path <path> --provider exchange`. The first run fails or is provisional while
   its model request is pending; that is expected.
2. Run `repo-doctor exchange pending`. For each pending key launch the `doctor-responder` subagent, wait, then
   run step 1 again. Repeat at most 3 times.
3. Show the explanation the command prints, in plain language.
