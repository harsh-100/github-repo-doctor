# Agents

Agents run **sequentially**: static tools, then code, security and testing agents, then the reviewer.
Every agent returns structured `Finding`s (`core/models.py`).

## Finding

`id`, `agent`, `category` (`area/subtype`), `severity` (critical..info), `title`, `description`,
`file_path`, `line`, `evidence`, `suggested_fix`, `confidence` (0-1), `verification`, plus a reviewer
note and an optional AI explanation.

Verification levels: **verified** (a deterministic check or tool established it), **supported** (model
claim whose quoted evidence was found in the file), **unverified** (evidence not found; severity capped at
medium, confidence capped at 0.3), **speculative** (reviewer flagged it; excluded from the health score),
**rejected** (removed; listed separately in the report).

## Agents

| Agent | Deterministic checks | Model review |
|---|---|---|
| code | Python AST: syntax errors, bare `except`, mutable defaults, large functions | selected source files, in capped batches |
| security | secret/token patterns, URL credentials, unsafe patterns (`eval`, shell, pickle, SQL string-building, `innerHTML`), un-gitignored secret files | risky files and CI/build files |
| testing | detects test frameworks and test paths (including `pytest` `testpaths`); flags modules **no test file references** (never just a missing filename); never runs tests | edge cases for a few untested modules |
| reviewer | file exists, line in range | confirms / marks speculative / rejects model findings; can drop unreasonable fixes; **cannot add findings** |

## Guarantees on model output (`agents/base.py`)

The file must be one that was actually sent; the line must be within the range sent; the quoted evidence
is searched for in the file and the line corrected to where it is found; secrets in the response are
redacted; invalid JSON is retried once, then reported. Items that fail validation are dropped and counted.

## Orchestrator (`core/orchestrator.py`)

Selects agents by command (`analyze`, `security`, `quality`, `tests`), isolates agent failures (reported,
not fatal), merges duplicates (a tool and a built-in check on the same line are one issue; the tool wins),
sorts by severity, assigns ids, and calls the explainer for tool findings. To add an agent, subclass
`Agent` and add it to `default_agents()`.
