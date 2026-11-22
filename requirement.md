# GitHub Repo Doctor
## Product Requirements & Technical Implementation Guide

**Project Type:** Open-source AI Agent / Developer Tool  
**Version:** 1.0 (MVP Specification)  
**Repository Name:** `github-repo-doctor`  
**Primary Goal:** Build an AI-powered autonomous repository analysis and code improvement agent that developers can install and run locally.

---

# 1. Product Vision

GitHub Repo Doctor is an open-source AI agent that helps developers understand, analyze, review, and improve their software repositories.

It should not behave like a normal chatbot where a user pastes code and receives suggestions.

Instead, it should operate as an agent that can:

1. Access and understand a local GitHub repository.
2. Inspect its directory structure and source code.
3. Understand the overall project architecture.
4. Identify bugs, code smells, security risks, and missing tests.
5. Use specialized agents to analyze different aspects of the repository.
6. Verify findings using available tools and deterministic checks.
7. Generate actionable reports.
8. Suggest code improvements.
9. Apply selected fixes only after explicit user approval.

**Core philosophy:**

The user should be able to clone the repository, configure their AI provider credentials, run one setup command, and start using the application.

The project should be easy enough for an individual developer to install without requiring Docker, cloud infrastructure, a database, or complicated configuration.

---

# 2. Target Users

The application should be useful for:

- Individual developers.
- Students working on university projects.
- Open-source contributors.
- Developers maintaining multiple repositories.
- Small software development teams.
- Developers exploring unfamiliar codebases.

The application should not assume that users are experts in AI or agent frameworks.

---

# 3. Main Features

## Feature 1: Repository Understanding

The agent should understand the repository before performing deeper analysis.

It should:

- Detect programming languages.
- Identify frameworks and libraries.
- Read package/configuration files.
- Understand directory structure.
- Identify entry points.
- Identify major modules and their relationships.
- Identify testing frameworks.
- Detect build and deployment configurations.
- Identify existing documentation.

Supported initial project types:

- Python
- JavaScript
- TypeScript
- React
- Next.js
- Node.js

The architecture should allow adding more languages later.

### Expected output

Generate:

`reports/PROJECT_OVERVIEW.md`

Include:

- Project description.
- Detected technologies.
- Directory structure.
- Important files.
- Application entry points.
- Dependencies.
- Architecture overview.
- Potential technical concerns.

## Feature 2: Code Quality Analysis

The Code Analysis Agent should inspect the source code.

Identify:

- Duplicate code.
- Unused imports and variables.
- Poor error handling.
- Unnecessary complexity.
- Large functions.
- Poor naming conventions.
- Missing input validation.
- Potential performance issues.
- Inconsistent coding practices.

Use existing static analysis tools where possible instead of relying exclusively on LLM reasoning.

Examples:

- Ruff for Python.
- ESLint for JavaScript and TypeScript.
- TypeScript compiler checks.
- Existing project test and lint commands.

The agent should distinguish between verified issues and potential improvements.

## Feature 3: Security Analysis

The Security Agent should inspect the repository for potential security issues.

Identify:

- Hardcoded credentials.
- Exposed API keys.
- Secrets accidentally committed to source files.
- Unsafe environment configuration.
- Insecure dependencies.
- SQL injection patterns.
- Unsafe command execution.
- Missing input validation.
- Common OWASP-related risks.

Use existing security tools when available.

Examples:

- Gitleaks.
- Bandit.
- Semgrep.
- npm audit.

Important:

Never send raw API keys, passwords, private keys, or detected secrets to the LLM provider.

Redact sensitive values before any model request.

Security findings should include severity, evidence, affected file, and suggested remediation.

## Feature 4: Testing Analysis

The Testing Agent should inspect the project's existing test setup.

It should:

- Detect testing frameworks.
- Identify existing test directories.
- Identify important modules without tests.
- Identify missing edge-case coverage.
- Detect obvious gaps in test organization.
- Optionally execute existing tests when explicitly enabled.

Examples:

- pytest.
- Jest.
- Vitest.
- unittest.

Do not claim that a module is untested simply because the agent did not find a conventional test filename. Inspect the project configuration and test discovery rules.

## Feature 5: Multi-Agent Review

The application should use an orchestrator that coordinates specialized agents.

### Proposed agents

**1. Orchestrator Agent**

Responsibilities:
- Understand the user's requested operation.
- Initialize repository context.
- Select the appropriate agents.
- Assign tasks.
- Collect results.
- Remove duplicate findings.
- Produce final output.

**2. Code Analysis Agent**

Responsibilities:
- Analyze code quality.
- Identify code smells.
- Detect possible bugs.
- Suggest improvements.

**3. Security Agent**

Responsibilities:
- Analyze security risks.
- Inspect dependencies.
- Identify potentially exposed credentials.
- Classify security findings.

**4. Testing Agent**

Responsibilities:
- Inspect testing configuration.
- Identify testing gaps.
- Run approved test commands.
- Suggest additional test cases.

**5. Reviewer Agent**

Responsibilities:
- Review findings from other agents.
- Check whether evidence supports the claims.
- Remove duplicate findings.
- Flag speculative issues.
- Verify that suggested fixes are reasonable.

### Agent execution flow

The agents should initially execute sequentially to keep the MVP simple and predictable.

Parallel execution can be introduced later.

Every agent should return structured output rather than unstructured paragraphs.

Suggested finding structure:

- Finding ID
- Agent name
- Category
- Severity
- Title
- Description
- File path
- Line number, if available
- Evidence
- Suggested fix
- Confidence score
- Verification status

---

# 4. CLI Commands

The application should provide a clean command-line interface.

Use `repo-doctor` as the CLI command.

### Initial commands

**1. Setup**

`repo-doctor setup`

Responsibilities:
- Check Python version.
- Check required dependencies.
- Create configuration directory.
- Create default configuration.
- Help users configure an AI provider.
- Validate provider connectivity.

**2. Analyze**

`repo-doctor analyze`

Perform a complete repository analysis.

Example:

`repo-doctor analyze --path ./my-project`

Expected operations:
- Repository discovery.
- Project understanding.
- Static analysis.
- Agent-based review.
- Finding verification.
- Report generation.

**3. Explain**

`repo-doctor explain`

Explain the project architecture in simple language.

Example:

`repo-doctor explain --path ./my-project`

**4. Security**

`repo-doctor security`

Run a security-focused analysis.

Example:

`repo-doctor security --path ./my-project`

**5. Quality**

`repo-doctor quality`

Run code quality analysis.

**6. Tests**

`repo-doctor tests`

Analyze test coverage and testing gaps.

**7. Report**

`repo-doctor report`

Display or regenerate the latest report.

**8. Fix**

`repo-doctor fix`

Propose fixes for selected findings.

The application must:

- Show the proposed changes.
- Explain the reason for each change.
- Request explicit approval.
- Apply only approved changes.
- Preserve the original files.
- Run relevant checks after modification.
- Report successful and unsuccessful fixes.

No automatic code modification should occur during normal analysis.

---

# 5. Example User Experience

The first-time experience should be extremely simple.

### Step 1: Clone the repository

`git clone https://github.com/USERNAME/github-repo-doctor.git`

`cd github-repo-doctor`

### Step 2: Install

`pip install -e .`

### Step 3: Configure

`repo-doctor setup`

The application should guide the user through AI provider configuration.

### Step 4: Analyze a project

`repo-doctor analyze --path /path/to/project`

### Step 5: Review results

The CLI should display:

- Repository information.
- Analysis progress.
- Agents currently executing.
- Issues discovered.
- Severity distribution.
- Report location.

Example terminal output:

```text
========================================
          GITHUB REPO DOCTOR
========================================

Repository: my-awesome-project
Language: Python, TypeScript

[1/5] Repository Discovery       DONE
[2/5] Static Analysis            DONE
[3/5] Code Analysis Agent        DONE
[4/5] Security Analysis Agent    DONE
[5/5] Reviewer Agent             DONE

========================================
             RESULTS
========================================

Critical Issues:  1
High Issues:      3
Medium Issues:    7
Low Issues:       5

Project Health: Needs Attention

Report generated:
reports/REPO_HEALTH_REPORT.md
========================================
```

The example above illustrates the desired interface. Actual results must always come from real analysis.

---

# 6. Technical Architecture

## Recommended Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| CLI framework | Typer |
| Terminal UI | Rich |
| Data validation | Pydantic |
| LLM integration | Provider abstraction |
| AI providers | Anthropic / OpenAI |
| Static analysis | Ruff, ESLint, Bandit etc. |
| File operations | Python pathlib |
| Git integration | GitPython or Git CLI |
| Configuration | TOML / YAML |
| Testing | pytest |

Avoid introducing unnecessary frameworks.

A custom lightweight orchestrator is preferred for the first version. We do not need a complicated multi-agent framework just to coordinate five analysis tasks.

## LLM Provider Architecture

Create a provider abstraction so that users are not permanently locked into one AI provider.

Initial providers:

- Anthropic API.
- OpenAI API.

Possible future providers:

- Ollama.
- Other OpenAI-compatible providers.

The user should configure their own API key.

Important billing distinction:

An API key and a Claude Code or ChatGPT subscription are not interchangeable. Do not assume that a consumer subscription automatically provides API access.

Provider configuration should be documented clearly.

Example configuration:

```toml
[llm]
provider = "anthropic"
model = "MODEL_NAME"
temperature = 0.1
```

API keys should be supplied through environment variables or a secure local configuration mechanism, never committed to Git.

Do not hardcode a specific model name throughout the application.

---

# 7. Proposed Repository Structure

Use a modular structure that makes future expansion easy.

```text
github-repo-doctor/
│
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── pyproject.toml
├── .gitignore
├── .env.example
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── INSTALLATION.md
│   ├── CONFIGURATION.md
│   └── AGENTS.md
│
├── src/
│   └── repo_doctor/
│       │
│       ├── __init__.py
│       ├── main.py
│       ├── cli.py
│       │
│       ├── core/
│       │   ├── orchestrator.py
│       │   ├── context.py
│       │   ├── models.py
│       │   └── settings.py
│       │
│       ├── agents/
│       │   ├── base.py
│       │   ├── code_agent.py
│       │   ├── security_agent.py
│       │   ├── testing_agent.py
│       │   └── reviewer_agent.py
│       │
│       ├── providers/
│       │   ├── base.py
│       │   ├── anthropic.py
│       │   └── openai.py
│       │
│       ├── analyzers/
│       │   ├── repository.py
│       │   ├── language.py
│       │   ├── dependencies.py
│       │   └── static_analysis.py
│       │
│       ├── security/
│       │   ├── redaction.py
│       │   └── permissions.py
│       │
│       ├── tools/
│       │   ├── file_reader.py
│       │   ├── git_tools.py
│       │   └── command_runner.py
│       │
│       ├── reports/
│       │   └── markdown.py
│       │
│       └── utils/
│           ├── logging.py
│           └── paths.py
│
├── tests/
│   ├── test_cli.py
│   ├── test_repository.py
│   ├── test_agents.py
│   ├── test_security.py
│   └── test_reports.py
│
└── examples/
    └── sample-project/
```

This is the proposed structure, not a requirement to create empty files for every module. Implement only the modules needed for each development phase.

---

# 8. Repository Scanning Rules

The repository scanner must be safe and efficient.

### Files to inspect

- Source code.
- Configuration files.
- Dependency manifests.
- Test files.
- Documentation.
- Build and deployment files.

### Directories to exclude by default

```text
.git/
node_modules/
.venv/
venv/
__pycache__/
dist/
build/
.next/
coverage/
vendor/
.cache/
```

### Files to exclude by default

- Binary files.
- Large generated files.
- Lockfiles from full LLM context, unless needed for dependency analysis.
- Environment files containing secrets.
- Private keys.
- Credentials and tokens.

The scanner should respect `.gitignore` where possible.

Provide configurable maximum file sizes and scanning limits.

### Important

Do not send an entire large repository to an LLM in one request.

Instead:

1. Build a repository inventory.
2. Identify relevant files.
3. Read files selectively.
4. Divide large files into manageable chunks.
5. Maintain summarized repository context.
6. Send only relevant information to each agent.

---

# 9. Security and Permission Model

This is an important part of the project.

Repo Doctor will inspect potentially sensitive source code.

Implement these rules:

1. Analysis is read-only by default.
2. Never execute arbitrary commands suggested by an LLM automatically.
3. Do not execute repository scripts without user consent.
4. Never expose secrets in LLM prompts or reports.
5. Restrict file access to the selected repository.
6. Prevent path traversal outside the repository.
7. Do not modify files without approval.
8. Do not upload repository contents to third-party services without informing the user.
9. Do not run destructive commands.
10. Log important operations without recording credentials.

For commands that execute tests or linters, show the command and ask for approval unless the user explicitly enabled a trusted execution mode.

---

# 10. Report Generation

The application should generate a structured Markdown report.

Primary output:

`reports/REPO_HEALTH_REPORT.md`

Suggested sections:

1. Executive Summary
2. Repository Overview
3. Technology Stack
4. Architecture Overview
5. Code Quality Findings
6. Security Findings
7. Testing Analysis
8. Critical Issues
9. Recommended Improvements
10. Suggested Fixes
11. Verification Results

Every important finding should contain:

- Severity.
- File location.
- Evidence.
- Explanation.
- Recommended action.

Avoid unsupported claims.

A finding without sufficient evidence should be marked as unverified or low confidence.

---

# 11. Development Phases

Build the application incrementally.

Do not attempt to implement every feature in one step.

## Phase 1 — Working Foundation

Goal: Make the application installable and usable.

Implement:

- Project structure.
- Python package.
- CLI.
- Setup command.
- Configuration handling.
- LLM provider abstraction.
- Repository scanner.
- Project overview generation.
- Basic Markdown reporting.

Acceptance criteria:

- A new user can install the application.
- Setup works without manual source-code changes.
- The user can configure an AI provider.
- The user can scan a local repository.
- The application generates a valid project overview.
- Errors are handled gracefully.

## Phase 2 — Analysis Agents

Implement:

- Code Analysis Agent.
- Security Agent.
- Testing Agent.
- Orchestrator.
- Structured agent outputs.
- Reviewer Agent.
- Finding deduplication.

Acceptance criteria:

- All agents receive appropriate repository context.
- Findings are structured.
- Agents do not invent file paths or line numbers.
- Reviewer can flag unsupported findings.
- The final report combines all results.

## Phase 3 — Real Tool Integration

Integrate static analysis tools.

Implement:

- Ruff.
- ESLint integration.
- Bandit.
- Gitleaks.
- Dependency audit integration where supported.

Acceptance criteria:

- Tools are detected before execution.
- Missing tools are explained.
- Tool execution is permission-controlled.
- Tool output is parsed into structured findings.
- The LLM can explain tool findings without changing the original evidence.

## Phase 4 — Fix Workflow

Implement:

- Fix command.
- Finding selection.
- Proposed patches.
- User approval.
- File backup or Git diff review.
- Post-fix verification.

Acceptance criteria:

- No file is modified without explicit approval.
- The application shows the proposed diff.
- Failed fixes can be rolled back.
- Verification results are reported.

## Phase 5 — Distribution and Polish

Implement:

- Complete README.
- Installation guide.
- Example repository.
- Example reports.
- CONTRIBUTING.md.
- SECURITY.md.
- Automated tests.
- CI workflow.
- Versioning and release preparation.

Acceptance criteria:

A developer who has never seen the project should be able to follow the README and successfully run their first repository analysis.

---

# 12. Testing Requirements

Tests are mandatory.

Cover:

- CLI command parsing.
- Repository path validation.
- File exclusion rules.
- Secret redaction.
- Provider configuration.
- Agent output validation.
- Finding deduplication.
- Markdown report generation.
- Permission checks.
- Fix approval workflow.

Use mocked LLM responses for automated tests.

The test suite should not require paid API calls.

Provide at least one small sample repository for integration testing.

---

# 13. MVP Scope: What We Build First

The first public version should prioritize reliability over feature count.

### Must have

- Local repository scanning.
- Python and JavaScript/TypeScript detection.
- Basic project overview.
- Three specialist analysis agents.
- One reviewer agent.
- Orchestrator.
- LLM API configuration.
- Static analysis integration.
- Markdown report.
- Safe read-only operation.
- Installation documentation.

### Not included in MVP

- Web dashboard.
- User accounts.
- Cloud hosting.
- Database.
- GitHub OAuth.
- Automatic pull requests.
- Continuous repository monitoring.
- Automatic background fixes.
- Team collaboration.
- Complex memory systems.

These can be considered in later versions.

---

# 14. Design Principles

Follow these principles throughout development:

**1. Agent-first, not chatbot-first**

The application should perform actual tasks through tools.

**2. Modular architecture**

Agents, tools, providers, analyzers and reporting should be independently maintainable.

**3. Provider independence**

Do not tightly couple the application to one LLM vendor.

**4. Evidence-based analysis**

Findings must be supported by code, tool output or verifiable evidence.

**5. Safe execution**

No uncontrolled shell commands or automatic file modifications.

**6. Simple setup**

A developer should be able to get started in a few minutes.

**7. Minimal dependencies**

Avoid unnecessary frameworks and infrastructure.

**8. Extensibility**

Adding another specialist agent should not require rewriting the orchestrator.

**9. Cost awareness**

Limit unnecessary model calls, use deterministic tools first, and provide configurable model settings.

**10. Good developer experience**

Clear CLI output, useful error messages, documentation and predictable behavior.

---

# 15. Definition of Done

The project can be considered ready for its first public release when:

- [ ] Installation works on Linux, macOS and Windows where supported.
- [ ] Setup command works.
- [ ] API provider configuration works.
- [ ] Repository scanning works.
- [ ] Python and JS/TS projects are detected.
- [ ] Project overview is generated.
- [ ] Specialist agents execute successfully.
- [ ] Reviewer verifies findings.
- [ ] Static analysis tools integrate correctly.
- [ ] Security redaction works.
- [ ] Markdown reports are generated.
- [ ] Fixes require explicit approval.
- [ ] Automated tests pass.
- [ ] README explains setup and usage.
- [ ] No secrets are committed.
- [ ] A first-time user can complete the workflow.

---

# 16. Instructions to the Coding Agent

You are responsible for implementing GitHub Repo Doctor based on this specification.

Before writing code:

1. Inspect the existing repository.
2. Understand its current structure.
3. Identify existing files and reusable components.
4. Check the available Python and system environment.
5. Create an implementation plan.
6. Identify technical risks and missing decisions.

During development:

- Work in small, verifiable steps.
- Prefer simple implementations.
- Do not overengineer.
- Follow the proposed architecture.
- Write tests alongside functionality.
- Do not create unnecessary abstractions.
- Do not hardcode API keys or model names.
- Do not silently skip errors.
- Keep the README updated.
- Run tests after meaningful changes.

If a technical decision is not explicitly specified, choose the simplest secure and maintainable solution and document the decision.

Do not implement future features just because they appear in the roadmap.

**Priority: Build a genuinely working Phase 1 first. Then implement subsequent phases incrementally.**

At the end of each phase, provide:

1. Features implemented.
2. Files created or modified.
3. Dependencies added.
4. Commands to run.
5. Tests executed and their results.
6. Known limitations.
7. Remaining tasks.

Do not claim that something works unless it has been tested.

---

# Final Product Goal

GitHub Repo Doctor should become a small, useful, open-source AI agent that developers can run on their own machines.

The goal is not to create another AI code review interface.

The goal is to demonstrate how multiple specialized AI agents, real development tools, structured workflows, and human approval can work together to complete a meaningful software engineering task.

**Build something developers can actually install, trust, and use.**