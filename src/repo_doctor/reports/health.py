"""REPO_HEALTH_REPORT.md and per-area reports."""
from __future__ import annotations

from datetime import UTC, datetime

from repo_doctor.core.models import AnalysisResult, Finding, ProjectProfile, Severity, Verification
from repo_doctor.security.redaction import redact

HEALTH_NAME = "REPO_HEALTH_REPORT.md"
REPORT_NAMES = {
    "analyze": HEALTH_NAME,
    "security": "SECURITY_REPORT.md",
    "quality": "QUALITY_REPORT.md",
    "tests": "TESTS_REPORT.md",
}
SCOPE_TITLES = {
    "analyze": "Full analysis",
    "security": "Security analysis",
    "quality": "Code quality analysis",
    "tests": "Testing analysis",
}
NL = "\n"


def _fence_safe(text: str) -> str:
    return text.replace("```", "'''")


def _finding_md(f: Finding) -> str:
    if f.file_path:
        loc = f"`{f.file_path}`" + (f":{f.line}" if f.line else "")
    else:
        loc = "_project-level_"
    lines = [
        f"#### {f.id} · {f.severity.value.capitalize()} · {f.title}",
        "",
        f"- **Location:** {loc}",
        f"- **Verification:** {f.verification.value} · confidence {f.confidence:.2f} · found by {f.agent}",
        f"- **Explanation:** {f.description or '_none_'}",
    ]
    if f.evidence:
        lines += ["- **Evidence:**", "", "  ```text", "  " + _fence_safe(f.evidence).replace(NL, NL + "  "), "  ```", ""]
    if f.explanation:
        lines.append(f"- **AI explanation:** {f.explanation}")
    lines.append(f"- **Recommended action:** {f.suggested_fix or '_none provided_'}")
    if f.review_note:
        lines.append(f"- **Reviewer note:** {f.review_note}")
    return NL.join(lines) + NL


def _section(findings: list[Finding], area: str, empty: str) -> str:
    items = [f for f in findings if f.category.split("/")[0] == area]
    return NL.join(_finding_md(f) for f in items) if items else empty


def render_health(profile: ProjectProfile, result: AnalysisResult, narrative: str | None = None) -> str:
    c = result.severity_counts()
    fs = result.findings
    scope = SCOPE_TITLES[result.mode]
    langs = ", ".join(f"{k} ({v})" for k, v in profile.languages.items()) or "_None detected_"
    spec = [f for f in fs if f.verification == Verification.SPECULATIVE]
    st = {v: len([f for f in fs if f.verification == v]) for v in Verification}
    critical = [f for f in result.counted if f.severity == Severity.CRITICAL]
    top = [f for f in result.counted if f.suggested_fix and f.severity != Severity.INFO][:10]
    fixable = [f for f in result.counted if f.suggested_fix]
    mode_note = "" if result.mode == "analyze" else (
        f"{NL}> Scope: **{scope}** only. Other areas were not analysed.{NL}")
    llm_line = (f"LLM-assisted ({result.llm_calls} model calls)" if result.llm_used
                else "Deterministic checks only (no AI provider used)")
    errors = NL.join(f"- `{k}` failed: {v}" for k, v in result.agent_errors.items()) or "_None._"
    rejected = NL.join(
        f"- {f.id} `{f.file_path or 'project'}`: {f.title} — {f.review_note or 'rejected'}"
        for f in result.rejected) or "_None._"
    crit_md = NL.join(f"- **{f.id}** `{f.file_path or 'project'}`: {f.title}" for f in critical) or "_None._"
    top_md = NL.join(f"{i}. **{f.title}** ({f.severity.value}, {f.id}) — {f.suggested_fix}"
                     for i, f in enumerate(top, 1)) or "_None._"
    tool_rows = [f"| {t.name} | {t.status} | {t.detail or '-'} |" for t in result.tools]
    tools_md = (NL.join(["| Tool | Status | Details |", "|---|---|---|", *tool_rows]) if tool_rows
                else "_No external tools were run for this analysis._")
    notes = NL.join(f"- {n}" for n in result.notes) or "_None._"

    out = f"""# Repository Health Report: {profile.name}

_Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by GitHub Repo Doctor · {scope}_
{mode_note}
## 1. Executive Summary

**Project health: {result.health()}**

| Critical | High | Medium | Low | Info |
|---|---|---|---|---|
| {c['critical']} | {c['high']} | {c['medium']} | {c['low']} | {c['info']} |

- Analysis mode: {llm_line}
- Agents run: {', '.join(result.agents_run) or 'none'} + reviewer
- Findings: {len(fs)} reported ({st[Verification.VERIFIED]} verified, {st[Verification.SUPPORTED]} supported, \
{st[Verification.UNVERIFIED]} unverified, {len(spec)} speculative); {len(result.rejected)} rejected; \
{result.duplicates_merged} duplicates merged
- Health counts non-speculative findings only. It is a heuristic, not a guarantee.

## 2. Repository Overview

- **Name:** {profile.name}
- **Files scanned:** {len(profile.inventory.files)}
- **Entry points:** {', '.join(f'`{e}`' for e in profile.entry_points) or '_None detected_'}
- **Documentation:** {', '.join(f'`{d}`' for d in profile.docs) or '_None_'}

## 3. Technology Stack

- **Languages:** {langs}
- **Frameworks:** {', '.join(profile.frameworks) or '_None detected_'}
- **Package managers:** {', '.join(profile.package_managers) or '_None detected_'}
- **Test frameworks:** {', '.join(profile.test_frameworks) or '_None detected_'}

## 4. Architecture Overview

{narrative or '_No AI summary generated. See `PROJECT_OVERVIEW.md` for the deterministic overview._'}

## 5. Code Quality Findings

{_section(fs, 'quality', '_No code-quality findings (or this area was not analysed)._')}

## 6. Security Findings

{_section(fs, 'security', '_No security findings (or this area was not analysed)._')}

## 7. Testing Analysis

{_section(fs, 'testing', '_No testing findings (or this area was not analysed)._')}

## 8. Critical Issues

{crit_md}

## 9. Recommended Improvements

{top_md}

## 10. Suggested Fixes

{len(fixable)} finding(s) include a suggested fix. Nothing in your repository was changed; fixes are \
proposals only. The approval-based `fix` workflow is not implemented yet.

## 11. Verification Results

- **verified**: established by a deterministic check ({st[Verification.VERIFIED]})
- **supported**: model claim whose quoted evidence was found in the file ({st[Verification.SUPPORTED]})
- **unverified**: model claim, evidence not located; severity capped ({st[Verification.UNVERIFIED]})
- **speculative**: flagged by the reviewer ({len(spec)})
- **rejected**: removed by validation or review ({len(result.rejected)})

**External tools**

{tools_md}

Tool findings are `verified` and use the tool's own message as evidence. Built-in pattern/AST checks \
and model review cover the rest; a tool and a built-in check flagging the same line are merged.

**Rejected findings**

{rejected}

**Agent errors**

{errors}

**Notes and coverage**

{notes}
"""
    return redact(out)
