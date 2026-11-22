"""report.md: rendered from ReportData only (the same data that feeds report.html)."""
from __future__ import annotations

import re

from repo_doctor.core.models import Finding, Severity
from repo_doctor.reports.data import ReportData, fmt_duration
from repo_doctor.reports.workspace import atomic_write_text  # re-exported for callers
from repo_doctor.security.redaction import redact

NL = "\n"
SEV_LABEL = {s.value: s.value.capitalize() for s in Severity}
__all__ = ["atomic_write_text", "esc", "fence", "render_markdown"]


def esc(text: str) -> str:
    """Neutralise raw HTML in repository-derived text (Markdown viewers may render it)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fence(text: str, lang: str = "text") -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}{NL}{text}{NL}{ticks}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = [("| " + " | ".join(str(c).replace("|", "\\|").replace(NL, " ") for c in r) + " |") for r in rows]
    return NL.join([head, sep, *body])


def _anchor(fid: str) -> str:
    return fid.lower()


def _finding(data: ReportData, f: Finding) -> str:
    t = data.text_for(f.id)
    lines = [f'<a id="{_anchor(f.id)}"></a>', f"#### {f.id} · {esc(t.title)}", "",
             f"**{SEV_LABEL[f.severity.value]}** · {t.verification_label} · confidence {round(f.confidence * 100)}%"
             + (f" · {t.location}" if t.location else ""), "",
             f"**What we found.** {esc(t.what)}", "", f"**Why it matters.** {esc(t.why)}", "",
             f"**What could happen.** {esc(t.risk)}", ""]
    if t.context:
        lines += [f"> **Context:** {esc(t.context)}", ""]
    lines += [f"**How to fix it.** {esc(t.fix)}", ""]
    if f.suggested_fix and f.suggested_fix != t.fix:
        lines += [f"**Suggested fix.** {esc(f.suggested_fix)}", ""]
    if f.explanation:
        lines += [f"**Plain-language note (AI).** {esc(f.explanation)}", ""]
    lines += [f"*{t.verification_meaning}*" + (f" Reviewer note: {esc(f.review_note)}" if f.review_note else ""), ""]
    if f.evidence:
        lines += ["<details><summary>Technical evidence</summary>", "", fence(redact(f.evidence)),
                  "", f"Technical description: {esc(f.description)}", "", f"Found by: {esc(f.agent)} · category `{esc(f.category)}`",
                  "", "</details>", ""]
    return NL.join(lines)


def _area_section(data: ReportData, area: str, intro: str, extra: list[str]) -> str:
    items = [f for f in data.result.findings if f.category.split("/")[0] == area]
    lines = [intro, ""]
    if items:
        lines.append(_table(["ID", "Severity", "What", "Where"], [
            [f"[{f.id}](#{_anchor(f.id)})", SEV_LABEL[f.severity.value], esc(data.text_for(f.id).title),
             data.text_for(f.id).location or "-"] for f in items]))
    else:
        lines.append("_Nothing was reported in this area by the checks that ran._")
    return NL.join(lines + ["", *extra]).rstrip()


def render_markdown(data: ReportData) -> str:
    r, p, s = data.result, data.project, data.summary
    c = r.severity_counts()
    vc = data.verification_counts()
    fs = r.findings
    scope = {"analyze": "Full analysis", "security": "Security analysis", "quality": "Code quality analysis",
             "tests": "Testing analysis"}[data.run.mode]
    langs = ", ".join(f"{k} ({v} files)" for k, v in data.methodology.languages.items()) or "none detected"
    llm = (f"{data.run.provider} ({data.run.model}), {data.run.llm_calls} model call(s)" if data.run.llm_used
           else "none (built-in checks and external tools only)")
    out: list[str] = [f"# Repo Doctor report: {esc(p.name)}", "",
                      f"_{scope} · {data.run.created_at[:16].replace('T', ' ')} UTC · run `{data.run.id}`_", "",
                      "[Project history](../../index.html) · [HTML version](report.html)", ""]
    if data.run.provisional:
        out += ["> **Provisional report.** Some model answers were still pending when this was written, so it uses "
                "built-in checks and tools only. A later run replaces it as the latest report.", ""]

    out += ["## 1. Project overview", "",
            "Repo Doctor examined your project to understand its structure, code quality, security, and testing "
            "practices. Here is what it found and what it recommends improving.", "",
            _table(["", ""], [
                ["Project", esc(p.name)], ["Location", f"`{esc(p.root)}`"], ["Languages", langs],
                ["Frameworks", ", ".join(data.frameworks) or "none detected"],
                ["Files analysed", str(data.methodology.files_scanned)],
                ["Analysis time", f"{data.run.created_at[:16].replace('T', ' ')} UTC ({fmt_duration(data.run.duration_seconds)})"],
                ["Mode", scope], ["Agents", ", ".join(data.methodology.agents)],
                ["External tools", ", ".join(f"{t['name']} ({t['status']})" for t in data.methodology.tools) or "none run"],
                ["AI model", llm]]), ""]
    if data.narrative:
        out += ["**Project description (AI-written summary):**", "", esc(data.narrative), ""]

    out += ["## 2. Executive summary", "", f"**Overall status: {s.status_headline}.** {s.status_meaning}", "",
            esc(s.overview), ""]
    if s.top_issue:
        out += [f"- **Most important issue:** {esc(s.top_issue)}"]
    if s.strengths:
        out += [f"- **Biggest strengths:** {'; '.join(s.strengths)}."]
    if s.attention_areas:
        out += [f"- **Areas needing attention:** {', '.join(s.attention_areas)}."]
    if s.first_action:
        out += [f"- **Recommended first action:** {esc(s.first_action)}."]
    out += ["", "_The status is a rough guide based on the findings listed here. It is not a guarantee of safety or quality._", ""]

    out += ["## 3. Project health dashboard", "",
            _table(["Critical", "High", "Medium", "Low", "Total"],
                   [[c["critical"], c["high"], c["medium"], c["low"], sum(c[k] for k in ("critical", "high", "medium", "low"))]]),
            "", _table(["Confirmed", "Likely", "Unconfirmed", "Speculative"],
                       [[vc["verified"], vc["supported"], vc["unverified"], vc["speculative"]]]), ""]
    if sum(c.values()):
        out += ["![Findings by severity](assets/severity.svg)", "", "![Findings by verification status](assets/verification.svg)", ""]
    if data.area_counts():
        out += ["![Findings by area](assets/area.svg)", ""]
    out += ["**What the numbers mean.** Severity says how serious a finding could be. *Confirmed* findings come from a "
            "tool or an exact check of the code. *Likely* and *Unconfirmed* findings come from the AI model and need "
            "your judgement. *Speculative* findings were doubted by the reviewer and are not counted in the totals.", ""]

    out += ["## 4. What is working well", ""]
    out += ([f"- **{x.title}.** {x.evidence}" for x in data.strengths] if data.strengths else
            ["_No positive observations could be backed by evidence in this run._"])
    out += [""]

    out += ["## 5. Findings: what needs attention", ""]
    shown = False
    for sev in ("critical", "high", "medium", "low", "info"):
        group = [f for f in fs if f.severity.value == sev]
        if group:
            shown = True
            out += [f"### {SEV_LABEL[sev]} ({len(group)})", ""] + [_finding(data, f) for f in group]
    if not shown:
        out += ["_No findings were reported._", ""]

    out += ["## 6. Code quality", "", _area_section(
        data, "quality", "Maintainability, error handling and static-analysis results.", []), ""]
    sec_extra = []
    sec_tools = [t for t in data.methodology.tools if t["name"] in ("bandit", "gitleaks", "npm-audit")]
    if sec_tools:
        sec_extra = ["**Security tools:** " + ", ".join(f"{t['name']} ({t['status']})" for t in sec_tools)]
    out += ["## 7. Security review", "", _area_section(
        data, "security", "Potential secrets, risky operations and dependency risks. *Confirmed* means a tool or exact "
        "check found it; whether it is *exploitable* is always a human decision.", sec_extra), ""]

    t = data.testing
    test_lines = [f"- **Test frameworks:** {', '.join(t.frameworks) or 'none detected'}",
                  f"- **Test files found:** {t.test_file_count}"]
    if t.test_files:
        test_lines.append("- **Examples:** " + ", ".join(f"`{esc(x)}`" for x in t.test_files[:6]))
    test_lines.append("- **Coverage:** not measured by Repo Doctor, so no coverage percentage is claimed.")
    out += ["## 8. Testing review", "", _area_section(
        data, "testing", "What testing exists and where it may be thin. A module is only called untested when no "
        "test file refers to it.", [""] + test_lines), ""]

    a = data.architecture
    arch = []
    if a.modules:
        arch += [_table(["Folder", "Files"], [[f"`{esc(m['name'])}/`", m["files"]] for m in a.modules]), ""]
    arch += [f"- **Entry points:** {', '.join(f'`{esc(e)}`' for e in a.entry_points) or 'none detected'}",
             f"- **Dependencies:** {a.runtime_dependencies} runtime, {a.dev_dependencies} development "
             f"({', '.join(f'`{m}`' for m in a.manifests) or 'no manifests'})"]
    arch += [f"- {esc(o)}" for o in a.observations]
    out += ["## 9. Architecture and project structure", "",
            "Only what Repo Doctor actually discovered is described here.", "", *arch, ""]

    out += ["## 10. Recommended action plan", "",
            _table(["Priority", "Action", "Details", "Findings"], [
                [x.priority, esc(x.title), esc(x.detail), ", ".join(f"[{i}](#{_anchor(i)})" for i in x.finding_ids) or "-"]
                for x in data.actions]), ""]

    m = data.methodology
    out += ["## 11. Analysis methodology and limitations", "", "**What ran**", "",
            f"- Agents: {', '.join(m.agents)}", f"- AI model: {llm}",
            f"- Files inspected: {m.files_scanned}"
            + (f" (skipped: {', '.join(f'{k}: {v}' for k, v in m.skipped.items())})" if m.skipped else ""), ""]
    if m.tools:
        out += [_table(["Tool", "Status", "Details"], [[x["name"], x["status"], esc(x["detail"] or "-")] for x in m.tools]), ""]
    else:
        out += ["_No external tools were run._", ""]
    out += ["**How findings were verified.** Tool and exact-check findings are *Confirmed*. For AI-model findings the "
            "quoted code must exist in the named file and line (otherwise the finding is marked *Unconfirmed* and its "
            "severity is capped); a separate reviewer step can flag or reject them. Duplicates are merged "
            f"({r.duplicates_merged} merged in this run, {len(r.rejected)} rejected).", ""]
    if m.notes:
        out += ["**Coverage notes**", ""] + [f"- {esc(n)}" for n in m.notes] + [""]
    out += ["**Limitations**", ""] + [f"- {esc(x)}" for x in m.limitations] + [""]
    out += ["---", f"_Generated by Repo Doctor {data.run.tool_version}. Machine-readable data: `findings.json` "
            "(this report and the HTML version are both built from it)._", ""]
    return redact(NL.join(out))
