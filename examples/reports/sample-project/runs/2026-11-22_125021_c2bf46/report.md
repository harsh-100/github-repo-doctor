# Repo Doctor report: sample-project

_Full analysis · 2026-11-22 12:50 UTC · run `2026-11-22_125021_c2bf46`_

[Project history](../../index.html) · [HTML version](report.html)

## 1. Project overview

Repo Doctor examined your project to understand its structure, code quality, security, and testing practices. Here is what it found and what it recommends improving.

|  |  |
|---|---|
| Project | sample-project |
| Location | `/path/to/sample-project` |
| Languages | Python (2 files), JavaScript (1 files) |
| Frameworks | Express, Flask, React |
| Files analysed | 6 |
| Analysis time | 2026-11-22 12:50 UTC (under 1 second) |
| Mode | Full analysis |
| Agents | code, security, testing, reviewer |
| External tools | none run |
| AI model | none (built-in checks and external tools only) |

## 2. Executive summary

**Overall status: Needs urgent review.** At least one critical finding was reported. Check whether it is real.

Repo Doctor examined sample-project to understand its structure, code quality, security and testing practices. It reported 3 finding(s): 1 critical, 2 high, 0 medium, 0 low.

- **Most important issue:** A string that looks like an API key or token is in the code (`src/app.py`:5). It may be intentional test or example data, so please confirm whether it is real.
- **Biggest strengths:** The project has automated tests; There is a README.
- **Areas needing attention:** Security (3).
- **Recommended first action:** Check whether the credential-like values are real and rotate any that are (2 places).

_The status is a rough guide based on the findings listed here. It is not a guarantee of safety or quality._

## 3. Project health dashboard

| Critical | High | Medium | Low | Total |
|---|---|---|---|---|
| 1 | 2 | 0 | 0 | 3 |

| Confirmed | Likely | Unconfirmed | Speculative |
|---|---|---|---|
| 3 | 0 | 0 | 0 |

![Findings by severity](assets/severity.svg)

![Findings by verification status](assets/verification.svg)

![Findings by area](assets/area.svg)

**What the numbers mean.** Severity says how serious a finding could be. *Confirmed* findings come from a tool or an exact check of the code. *Likely* and *Unconfirmed* findings come from the AI model and need your judgement. *Speculative* findings were doubted by the reviewer and are not counted in the totals.

## 4. What is working well

- **The project has automated tests.** 1 test file(s) were found using Jest, unittest/pytest (inferred from test files).
- **There is a README.** A README file is present, which helps newcomers get started.
- **Dependencies are declared in manifest files.** `package.json`, `requirements.txt`
- **Python files parse cleanly.** No syntax errors in 2 Python file(s).

## 5. Findings: what needs attention

### Critical (1)

<a id="f-001"></a>
#### F-001 · A string that looks like an API key or token is in the code

**Critical** · Confirmed · confidence 90% · `src/app.py`:5

**What we found.** A value matching a known credential format is written directly in `src/app.py`. (The value is hidden in this report.)

**Why it matters.** Secrets stored in code are copied with the code, so they spread to everyone and every place the repository goes.

**What could happen.** If the value is real, anyone who can read this repository could use it to access the service it belongs to, which could mean unauthorised use, cost, or data exposure.

> **Context:** A comment next to it calls this value "fake". If it is deliberately fake, this is probably not a real problem. Confirm that, and keep real secrets out of examples.

**How to fix it.** Revoke/rotate the credential and load it from an environment variable.

*Confirmed by a tool or an exact check of the code.*

<details><summary>Technical evidence</summary>

```text
API_KEY = "[REDACTED]"  # fake key for redaction tests
```

Technical description: A string matching a known credential format is committed. (Value redacted.)

Found by: security · category `security/hardcoded-secret`

</details>

### High (2)

<a id="f-002"></a>
#### F-002 · A secrets file is not protected from being committed

**High** · Confirmed · confidence 90% · `.env`

**What we found.** `.env` usually holds secrets, and no `.gitignore` rule excludes it. Repo Doctor did not read its contents.

**Why it matters.** Files like this are easy to commit by accident.

**What could happen.** If it contains real secrets and gets committed, they become visible to everyone with access.

**How to fix it.** Add `.env` to .gitignore; if already committed, rotate its secrets and purge it from history.

*Confirmed by a tool or an exact check of the code.*

<details><summary>Technical evidence</summary>

```text
.env present; no matching .gitignore rule
```

Technical description: A secrets-bearing file exists in the repository directory and is not matched by .gitignore, so it may be committed. (Contents were not read.)

Found by: security · category `security/unsafe-config`

</details>

<a id="f-003"></a>
#### F-003 · A password appears inside a connection address

**High** · Confirmed · confidence 90% · `src/app.py`:6

**What we found.** A web or database address in `src/app.py` contains a username and password.

**Why it matters.** Addresses are logged, shared and copied freely, so a password inside one leaks easily.

**What could happen.** If the password is real, someone with the repository could log in to that service.

**How to fix it.** Move credentials to environment variables or a secret manager.

*Confirmed by a tool or an exact check of the code.*

<details><summary>Technical evidence</summary>

```text
DB_URL = "postgres://admin:[REDACTED]@db.local/app"
```

Technical description: A connection string contains a username and password.

Found by: security · category `security/hardcoded-secret`

</details>

## 6. Code quality

Maintainability, error handling and static-analysis results.

_Nothing was reported in this area by the checks that ran._

## 7. Security review

Potential secrets, risky operations and dependency risks. *Confirmed* means a tool or exact check found it; whether it is *exploitable* is always a human decision.

| ID | Severity | What | Where |
|---|---|---|---|
| [F-001](#f-001) | Critical | A string that looks like an API key or token is in the code | `src/app.py`:5 |
| [F-002](#f-002) | High | A secrets file is not protected from being committed | `.env` |
| [F-003](#f-003) | High | A password appears inside a connection address | `src/app.py`:6 |

## 8. Testing review

What testing exists and where it may be thin. A module is only called untested when no test file refers to it.

_Nothing was reported in this area by the checks that ran._


- **Test frameworks:** Jest, unittest/pytest (inferred from test files)
- **Test files found:** 1
- **Examples:** `tests/test_app.py`
- **Coverage:** not measured by Repo Doctor, so no coverage percentage is claimed.

## 9. Architecture and project structure

Only what Repo Doctor actually discovered is described here.

| Folder | Files |
|---|---|
| `src/` | 2 |
| `tests/` | 1 |

- **Entry points:** `src/index.js`, `src/app.py`
- **Dependencies:** 4 runtime, 1 development (`package.json`, `requirements.txt`)
- The largest top-level folder is `src/` with 2 of 6 files (33%).

## 10. Recommended action plan

| Priority | Action | Details | Findings |
|---|---|---|---|
| 1 | Check whether the credential-like values are real and rotate any that are (2 places) | Revoke/rotate the credential and load it from an environment variable. | [F-001](#f-001), [F-003](#f-003) |
| 2 | Add secrets files to .gitignore | Add `.env` to .gitignore; if already committed, rotate its secrets and purge it from history. | [F-002](#f-002) |

## 11. Analysis methodology and limitations

**What ran**

- Agents: code, security, testing, reviewer
- AI model: none (built-in checks and external tools only)
- Files inspected: 6 (skipped: excluded directory: 1, sensitive file: 1, binary: 1)

_No external tools were run._

**How findings were verified.** Tool and exact-check findings are *Confirmed*. For AI-model findings the quoted code must exist in the named file and line (otherwise the finding is marked *Unconfirmed* and its severity is capped); a separate reviewer step can flag or reject them. Duplicates are merged (0 merged in this run, 0 rejected).

**Limitations**

- Repo Doctor reads code; it does not run your application or (unless you ask) your tests.
- Test coverage was not measured. Test findings are based on test files and references, not on running tests.
- Only Python, JavaScript and TypeScript are analysed in depth.
- Pattern-based security checks can miss problems and can flag harmless code; findings need human judgement.
- AI-model findings are judgement calls. Only `Confirmed` findings come from a tool or an exact check.
- No AI model was used in this run, so only built-in checks and external tools contributed.

---
_Generated by Repo Doctor 0.3.0. Machine-readable data: `findings.json` (this report and the HTML version are both built from it)._
