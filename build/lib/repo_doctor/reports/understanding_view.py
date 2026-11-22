"""Render the 'Understanding This Project' section (Markdown and HTML) from ReportData only."""
from __future__ import annotations

from repo_doctor.core.models import ProjectUnderstanding
from repo_doctor.core.understanding import workflow_ascii
from repo_doctor.reports.data import ReportData

NL = "\n"
FEATURE_LABEL = {"in-code": "In the code", "documented": "Documented only", "planned": "Planned (documented)"}
TECH_LABEL = {"confirmed": "Used in the code", "tests-only": "Only in tests/examples",
              "declared-only": "Declared, no usage found", "mentioned": "Mentioned in the docs"}
KIND_LABEL = {"language": "Language", "framework": "Framework", "library": "Library", "ai": "AI integration",
              "database": "Database", "service": "External service", "tool": "Tool"}
WORKFLOW_CAPTION = {
    "docs": "The commands in the order the project's own documentation presents them.",
    "components": "The project's commands in the order they are defined in the code (not necessarily a workflow).",
    "ai": "Workflow as explained by an AI model from the cited files.",
}
NOT_STATED = "The documentation does not state this."
SOURCE_NOTE = {
    "ai": "Plain-language wording written by an AI model from the evidence below. Repo Doctor has not run the "
          "project or verified that its features work.",
    "docs": "Built from the project's own documentation and code (no AI model was used). Repo Doctor has not run the "
            "project or verified that its features work.",
}
SECTIONS = [("what", "What is this project?"), ("problem", "What problem does it solve?"),
            ("features", "What can you do with it?"), ("audience", "Who can benefit from it?"),
            ("tech", "What are the main technologies?"), ("how", "How does it work?"),
            ("start", "How can you get started?"), ("value", "What makes it useful?"),
            ("maturity", "How mature does it look?")]


def intro_note(u: ProjectUnderstanding) -> str:
    return SOURCE_NOTE["ai" if u.ai_used else "docs"]


def _sources(u: ProjectUnderstanding) -> str:
    return ", ".join(u.getting_started.sources)


def markdown_section(data: ReportData, number: int, esc) -> str:
    """`esc` neutralises raw HTML in repository-derived text."""
    head = f"## {number}. Understanding this project"
    u = data.understanding
    if u is None:
        why = ("This section is part of full analyses. Run `repo-doctor analyze` to include it."
               if data.run.mode != "analyze" else "It could not be generated for this run (see the methodology section).")
        return NL.join([head, "", f"_{why}_", ""])
    out = [head, "", f"_{intro_note(u)}_", "", "### What is this project?", "", esc(u.what) or f"_{NOT_STATED}_", "",
           "### What problem does it solve?", "", esc(u.problem) if u.problem else f"_{NOT_STATED}_", ""]
    out += ["### What can you do with it?", ""]
    if u.features:
        for f in u.features:
            ev = ", ".join(f"`{esc(e)}`" for e in f.evidence[:2])
            out.append(f"- {esc(f.text)} **[{FEATURE_LABEL[f.status]}]**" + (f" ({ev})" if ev else ""))
        out += ["", "_\"In the code\" means a matching file or command exists. \"Documented only\" means only the "
                "documentation mentions it. Nothing was run._"]
    else:
        out.append("_No features could be backed by evidence in the repository._")
    out += ["", "### Who can benefit from it?", ""]
    if u.audience:
        out += [f"- {esc(a.text)} _({'stated by the project' if a.basis == 'documented' else 'inferred by Repo Doctor'})_"
                for a in u.audience]
    else:
        out.append(f"_{NOT_STATED}_")
    out += ["", "### What are the main technologies?", ""]
    if u.technologies:
        out += ["| Technology | Type | How we know |", "|---|---|---|"]
        out += [f"| {esc(t.name)} | {KIND_LABEL.get(t.kind, t.kind)} | {TECH_LABEL[t.confidence]}: {esc(t.evidence)} |"
                for t in u.technologies]
    else:
        out.append("_None identified._")
    out += ["", "### How does it work?", ""]
    if len(u.workflow) >= 2:
        out += [f"_{WORKFLOW_CAPTION.get(u.workflow_source, '')}_", "", "```text", workflow_ascii(u.workflow), "```"]
        out += [f"- **{esc(s.title)}**" + (f": {esc(s.detail)}" if s.detail else "") for s in u.workflow]
    else:
        out.append("_Repo Doctor could not find enough evidence to describe a workflow._")
    gs = u.getting_started
    out += ["", "### How can you get started?", "", "_Repo Doctor has not run any of these commands._", ""]
    for label, items, code in (("Prerequisites", gs.prerequisites, False), ("Install", gs.install, True),
                               ("Configure", gs.configure, False), ("Basic usage", gs.usage, True),
                               ("API keys and paid services", gs.paid_or_keys, False),
                               ("Limitations", gs.limitations, False)):
        if items:
            out += [f"**{label}**", ""]
            out += (["```bash", *[i.replace("```", "'''") for i in items], "```"] if code else [f"- {esc(i)}" for i in items])
            out.append("")
    if not any([gs.prerequisites, gs.install, gs.configure, gs.usage]):
        out += ["_No setup instructions were found in the documentation._", ""]
    elif gs.sources:
        out += [f"_Sources: {esc(_sources(u))}_", ""]
    out += ["### What makes it useful?", "", esc(u.value) if u.value else f"_{NOT_STATED}_", ""]
    m = u.maturity
    out += ["### How mature does it look?", ""]
    if m:
        out += [f"**{m.level.capitalize()}.** This is Repo Doctor's assessment from the evidence below, not an official "
                "project status.", ""]
        if m.positives:
            out.append("- **Signs of maturity:** " + ", ".join(m.positives))
        if m.gaps:
            out.append("- **Missing or weak:** " + ", ".join(m.gaps))
        if m.activity:
            out.append(f"- **Activity:** {esc(m.activity)}")
        out.append("")
    out += ["### Evidence and uncertainty", ""]
    if u.evidence_files:
        out += ["Files this section is based on: " + ", ".join(f"`{esc(f)}`" for f in u.evidence_files[:12]) + ".", ""]
    out += [f"- {esc(x)}" for x in u.uncertainty] or ["_No particular uncertainties were noted._"]
    out.append("")
    return NL.join(out)


# ------------------------------------------------------------------------------------------------ HTML
def html_section(data: ReportData, e, inline) -> str:
    """`e` escapes text; `inline` escapes and allows `code`/**bold**."""
    u = data.understanding
    if u is None:
        why = ("This section is part of full analyses. Run <code>repo-doctor analyze</code> to include it."
               if data.run.mode != "analyze" else "It could not be generated for this run (see the methodology section).")
        return f'<section id="understanding"><h2>Understanding this project</h2><p class="muted">{why}</p></section>'

    def card(title: str, body: str, cls: str = "") -> str:
        return f'<div class="ucard {cls}"><h3>{e(title)}</h3>{body}</div>'

    ai = '<span class="badge ver Confirmed">AI-written wording</span>' if u.ai_used else (
        '<span class="badge ver">From the docs and code</span>')
    what = (f'<p class="lead">{e(u.what)}</p>' if u.what else f'<p class="muted">{NOT_STATED}</p>')
    problem = f"<p>{e(u.problem)}</p>" if u.problem else f'<p class="muted">{NOT_STATED}</p>'
    if u.features:
        feats = "".join(
            f'<li><span class="badge ver feat-{f.status}">{FEATURE_LABEL[f.status]}</span> {inline(f.text)}'
            + (f' <span class="muted">({e(", ".join(f.evidence[:2]))})</span>' if f.evidence else "") + "</li>"
            for f in u.features)
        features = (f'<ul class="plain cols">{feats}</ul><p class="muted">&ldquo;In the code&rdquo; means a matching file or '
                    "command exists; &ldquo;Documented only&rdquo; means only the documentation mentions it. Nothing was run.</p>")
    else:
        features = '<p class="muted">No features could be backed by evidence in the repository.</p>'
    if u.audience:
        aud = "".join(f'<li>{e(a.text)} <span class="badge ver">{"Stated by the project" if a.basis == "documented" else "Inferred by Repo Doctor"}</span></li>'
                      for a in u.audience)
        audience = f'<ul class="plain">{aud}</ul>'
    else:
        audience = f'<p class="muted">{NOT_STATED}</p>'
    if u.technologies:
        rows = "".join(f"<tr><td>{e(t.name)}</td><td>{KIND_LABEL.get(t.kind, t.kind)}</td>"
                       f"<td>{TECH_LABEL[t.confidence]}<br><span class='muted'>{e(t.evidence)}</span></td></tr>"
                       for t in u.technologies)
        tech = ('<div class="tablewrap"><table><caption class="muted">Technologies and how Repo Doctor knows</caption>'
                '<thead><tr><th scope="col">Technology</th><th scope="col">Type</th><th scope="col">Evidence</th></tr></thead>'
                f"<tbody>{rows}</tbody></table></div>")
    else:
        tech = '<p class="muted">None identified.</p>'
    if len(u.workflow) >= 2:
        steps = "".join(f'<li class="step"><strong>{e(s.title)}</strong>' + (f"<span>{e(s.detail)}</span>" if s.detail else "") + "</li>"
                        for s in u.workflow)
        how = (f'<p class="muted">{e(WORKFLOW_CAPTION.get(u.workflow_source, ""))}</p>'
               f'<ol class="flow" aria-label="Workflow">{steps}</ol>')
    else:
        how = '<p class="muted">Repo Doctor could not find enough evidence to describe a workflow.</p>'
    gs = u.getting_started
    blocks = []
    for label, items, code in (("Prerequisites", gs.prerequisites, False), ("Install", gs.install, True),
                               ("Configure", gs.configure, False), ("Basic usage", gs.usage, True),
                               ("API keys and paid services", gs.paid_or_keys, False), ("Limitations", gs.limitations, False)):
        if items:
            body = (f"<pre>{e(chr(10).join(items))}</pre>" if code else
                    "<ul class='plain'>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>")
            blocks.append(f"<h4>{label}</h4>{body}")
    start = ("".join(blocks) if blocks else '<p class="muted">No setup instructions were found in the documentation.</p>')
    start += ('<p class="muted">Repo Doctor has not run any of these commands.'
              + (f" Sources: {e(_sources(u))}." if gs.sources else "") + "</p>")
    value = f"<p>{e(u.value)}</p>" if u.value else f'<p class="muted">{NOT_STATED}</p>'
    m = u.maturity
    maturity = ""
    if m:
        maturity = (f'<p><span class="pill fair" style="margin:0">{e(m.level.capitalize())}</span></p>'
                    '<p class="muted">Repo Doctor\'s assessment from the evidence below, not an official project status.</p><ul class="plain">'
                    + (f"<li><strong>Signs of maturity:</strong> {e(', '.join(m.positives))}</li>" if m.positives else "")
                    + (f"<li><strong>Missing or weak:</strong> {e(', '.join(m.gaps))}</li>" if m.gaps else "")
                    + (f"<li><strong>Activity:</strong> {e(m.activity)}</li>" if m.activity else "") + "</ul>")
    unc = "".join(f"<li>{e(x)}</li>" for x in u.uncertainty)
    evid = (f'<details><summary class="btn" style="display:inline-block">Evidence and uncertainty</summary>'
            f"<p>Files this section is based on: {', '.join(f'<code>{e(f)}</code>' for f in u.evidence_files[:12]) or 'none'}.</p>"
            f'<ul class="plain">{unc or "<li>No particular uncertainties were noted.</li>"}</ul></details>')
    return (f'<section id="understanding"><h2>Understanding this project</h2>'
            f'<div class="ugrid"><div class="ucard hero-card"><div class="muted" style="margin-bottom:6px">{ai} '
            f'{e(intro_note(u))}</div><h3>What is this project?</h3>{what}<h3>What problem does it solve?</h3>{problem}</div>'
            f'{card("What can you do with it?", features, "wide")}{card("Who can benefit from it?", audience)}'
            f'{card("What makes it useful?", value)}'
            f'{card("What are the main technologies?", tech, "wide")}{card("How does it work?", how, "wide")}'
            f'{card("How can you get started?", start, "wide")}'
            f'{card("How mature does it look?", maturity, "wide")}</div>{evid}</section>')
