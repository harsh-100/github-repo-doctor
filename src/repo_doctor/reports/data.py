"""ReportData: the single source of truth behind findings.json, report.md and report.html.

Everything a renderer shows comes from this model. Nothing here is invented: strengths, architecture and
methodology are derived only from what the analysis actually discovered.
"""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

from repo_doctor.core.models import AnalysisResult, Finding, ProjectProfile, Severity, Verification
from repo_doctor.reports.language import VERIFICATION_TEXT, context_note, explain
from repo_doctor.reports.workspace import SCHEMA_VERSION
from repo_doctor.security.redaction import redact

SEVERITY_ORDER = list(Severity)
AREAS = ("security", "quality", "testing")
AREA_TITLES = {"security": "Security", "quality": "Code quality", "testing": "Testing"}

STATUS = {  # health label -> (headline, one-line meaning)
    "Healthy": ("Looks healthy", "The checks that ran reported nothing that needs action."),
    "Fair": ("Mostly fine, with small improvements available",
             "Only low or medium findings were reported; nothing urgent."),
    "Needs Attention": ("Needs attention", "Some findings should be reviewed soon."),
    "Critical": ("Needs urgent review", "At least one critical finding was reported. Check whether it is real."),
}


def fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return "under 1 second"
    return f"{seconds:.0f}s" if seconds < 90 else f"{seconds / 60:.1f} min"


class RunInfo(BaseModel):
    id: str
    created_at: str
    duration_seconds: float
    mode: str
    tool_version: str
    provider: str | None = None
    model: str | None = None
    llm_used: bool = False
    llm_calls: int = 0
    provisional: bool = False  # written while model answers were still pending


class ProjectInfo(BaseModel):
    name: str
    slug: str
    root: str
    root_hash: str
    identity_hash: str
    remote: str | None = None
    source: dict | None = None  # {"kind": "github", "url", "owner", "repo", "branch", "commit", ...} for remote analyses


class Strength(BaseModel):
    title: str
    evidence: str


class Action(BaseModel):
    priority: int
    title: str
    detail: str
    finding_ids: list[str] = Field(default_factory=list)
    severity: str | None = None


class FindingText(BaseModel):
    id: str
    title: str
    what: str
    why: str
    risk: str
    fix: str
    context: str = ""
    verification_label: str
    verification_meaning: str
    location: str = ""


class Summary(BaseModel):
    status_label: str
    status_headline: str
    status_meaning: str
    overview: str
    top_issue: str | None = None
    strengths: list[str] = Field(default_factory=list)
    attention_areas: list[str] = Field(default_factory=list)
    first_action: str | None = None


class Architecture(BaseModel):
    modules: list[dict] = Field(default_factory=list)       # {name, files}
    entry_points: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    runtime_dependencies: int = 0
    dev_dependencies: int = 0
    observations: list[str] = Field(default_factory=list)


class TestingInfo(BaseModel):
    __test__ = False
    frameworks: list[str] = Field(default_factory=list)
    test_file_count: int = 0
    test_files: list[str] = Field(default_factory=list)     # sample
    coverage_measured: bool = False


class Methodology(BaseModel):
    agents: list[str] = Field(default_factory=list)
    tools: list[dict] = Field(default_factory=list)         # {name, status, detail}
    files_scanned: int = 0
    skipped: dict[str, int] = Field(default_factory=dict)
    languages: dict[str, int] = Field(default_factory=dict)
    scan_truncated: bool = False
    notes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReportData(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: RunInfo
    project: ProjectInfo
    summary: Summary
    strengths: list[Strength] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    finding_text: list[FindingText] = Field(default_factory=list)
    narrative: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    architecture: Architecture = Field(default_factory=Architecture)
    testing: TestingInfo = Field(default_factory=TestingInfo)
    methodology: Methodology = Field(default_factory=Methodology)
    result: AnalysisResult

    @property
    def understanding(self):
        return self.result.understanding

    # ---- convenience for renderers
    def text_for(self, finding_id: str) -> FindingText:
        return next(t for t in self.finding_text if t.id == finding_id)

    def verification_counts(self) -> dict[str, int]:
        c = Counter(f.verification.value for f in self.result.findings)
        return {v.value: c.get(v.value, 0) for v in Verification if v != Verification.REJECTED}

    def area_counts(self) -> dict[str, int]:
        return area_counts(self.result)


# ------------------------------------------------------------------ builders
def _s(title: str, evidence: str) -> Strength:
    return Strength(title=title, evidence=evidence)


def _strengths(profile: ProjectProfile, result: AnalysisResult) -> list[Strength]:
    inv = profile.inventory
    out: list[Strength] = []
    tests = [f for f in inv.files if f.kind == "test"]
    if tests:
        fw = f" using {', '.join(profile.test_frameworks)}" if profile.test_frameworks else ""
        out.append(_s("The project has automated tests", f"{len(tests)} test file(s) were found{fw}."))
    if any(PurePosixPath(f.path).name.lower().startswith("readme") for f in inv.files):
        out.append(_s("There is a README", "A README file is present, which helps newcomers get started."))
    if profile.docs and len(profile.docs) > 1:
        out.append(_s("There is extra documentation", f"{len(profile.docs)} documentation file(s) found."))
    if any(f.kind == "build" and ".github/workflows" in f.path for f in inv.files):
        out.append(_s("Automated checks run in CI", "A GitHub Actions workflow is present."))
    if profile.manifests:
        out.append(_s("Dependencies are declared in manifest files", ", ".join(f"`{m}`" for m in profile.manifests[:4])))
    if any(f.path == "tsconfig.json" for f in inv.files):
        out.append(_s("TypeScript is configured", "A `tsconfig.json` is present."))
    gitignored_secrets = inv.skipped.get("sensitive file", 0) - len(inv.sensitive_files)
    if gitignored_secrets > 0:
        out.append(_s("Secret files are kept out of git",
                            f"{gitignored_secrets} secrets file(s) such as `.env` are covered by `.gitignore`."))
    for run in result.tools:
        if run.status == "ran" and run.findings == 0:
            out.append(_s(f"{run.name} found no issues", f"{run.name} ran successfully and reported nothing."))
    py_files = profile.languages.get("Python", 0)
    if py_files and not any(f.title == "Syntax error" for f in result.findings) and "code" in result.agents_run:
        out.append(_s("Python files parse cleanly", f"No syntax errors in {py_files} Python file(s)."))
    return out[:8]


def _group_key(f: Finding) -> str:
    return f.category


def _actions(result: AnalysisResult, texts: dict[str, FindingText], profile: ProjectProfile) -> list[Action]:
    groups: dict[str, list[Finding]] = {}
    for f in result.counted:
        if f.severity != Severity.INFO:
            groups.setdefault(_group_key(f), []).append(f)

    def rank(item):
        _, fs = item
        sev = min(SEVERITY_ORDER.index(f.severity) for f in fs)
        verified = any(f.verification == Verification.VERIFIED for f in fs)
        area = AREAS.index(fs[0].category.split("/")[0]) if fs[0].category.split("/")[0] in AREAS else 9
        return (sev, not verified, area, -len(fs))

    actions: list[Action] = []
    for _, fs in sorted(groups.items(), key=rank)[:7]:
        top = fs[0]
        t = explain(top)
        where = f" ({len(fs)} places)" if len(fs) > 1 else ""
        actions.append(Action(priority=len(actions) + 1, title=t.action + where,
                              detail=texts[top.id].fix, finding_ids=[f.id for f in fs],
                              severity=min((f.severity for f in fs), key=SEVERITY_ORDER.index).value))
    missing = [t.name for t in result.tools if t.status == "skipped" and t.detail.startswith("not installed")]
    if missing:
        actions.append(Action(priority=len(actions) + 1, title="Install optional tools for deeper checks",
                              detail=f"Not installed: {', '.join(missing)}. See the Methodology section for install hints."))
    if not actions:
        actions.append(Action(priority=1, title="No urgent actions", detail="Re-run Repo Doctor after significant "
                              "changes to keep an eye on the project."))
    return actions


def _architecture(profile: ProjectProfile) -> Architecture:
    inv = profile.inventory
    total = len(inv.files) or 1
    mods = [{"name": k, "files": v} for k, v in list(profile.top_level_modules.items())[:12]]
    obs: list[str] = []
    if mods:
        big = mods[0]
        pct = round(100 * big["files"] / total)
        obs.append(f"The largest top-level folder is `{big['name']}/` with {big['files']} of {len(inv.files)} files ({pct}%).")
    if not profile.entry_points:
        obs.append("No application entry point was detected.")
    obs += [c for c in profile.concerns if c.startswith(("Could not parse manifest", "Scan hit"))]
    return Architecture(
        modules=mods, entry_points=profile.entry_points[:10], manifests=profile.manifests,
        runtime_dependencies=len([d for d in profile.dependencies if not d.dev]),
        dev_dependencies=len([d for d in profile.dependencies if d.dev]), observations=obs)


_LIMITATIONS = [
    "Repo Doctor reads code; it does not run your application or (unless you ask) your tests.",
    "Test coverage was not measured. Test findings are based on test files and references, not on running tests.",
    "Only Python, JavaScript and TypeScript are analysed in depth.",
    "Pattern-based security checks can miss problems and can flag harmless code; findings need human judgement.",
    "AI-model findings are judgement calls. Only `Confirmed` findings come from a tool or an exact check.",
]


def _methodology(profile: ProjectProfile, result: AnalysisResult, source: dict | None = None) -> Methodology:
    inv = profile.inventory
    notes = [n for n in result.notes if not n.startswith("WARNING")]
    warnings = [n.removeprefix("WARNING: ") for n in result.notes if n.startswith("WARNING")]
    limits = list(_LIMITATIONS)
    if not result.llm_used:
        limits.append("No AI model was used in this run, so only built-in checks and external tools contributed.")
    for t in result.tools:
        if t.status in ("skipped", "denied", "failed"):
            limits.append(f"{t.name}: {t.status}. {t.detail}")
    if inv.truncated:
        limits.append("The scan hit the file limit, so some files were not inspected.")
    if source:
        limits.append("The repository was analysed from a temporary shallow clone (latest commit only, no history) "
                      "that was deleted afterwards. Nothing from it was executed.")
    limits += [f"Warning: {w}" for w in warnings]
    return Methodology(
        agents=[*result.agents_run, "reviewer"], tools=[t.model_dump() for t in result.tools],
        files_scanned=len(inv.files), skipped=dict(inv.skipped), languages=dict(profile.languages),
        scan_truncated=inv.truncated, notes=notes, limitations=limits)


def area_counts(result: AnalysisResult) -> dict[str, int]:
    c = Counter(f.category.split("/")[0] for f in result.counted)
    return {a: c[a] for a in AREAS if c.get(a)}


def _summary(project: ProjectInfo, profile: ProjectProfile, result: AnalysisResult, strengths: list[Strength],
             actions: list[Action], texts: dict[str, FindingText]) -> Summary:
    label = result.health()
    headline, meaning = STATUS[label]
    c = result.severity_counts()
    parts = [f"Repo Doctor examined {project.name} to understand its structure, code quality, security and "
             "testing practices."]
    top = result.counted[0] if result.counted else None
    top_issue = None
    if top:
        t = texts[top.id]
        top_issue = f"{t.title}{(' (' + t.location + ')') if t.location else ''}."
        if t.context:
            top_issue += " It may be intentional test or example data, so please confirm whether it is real."
    areas = [f"{AREA_TITLES[a]} ({n})" for a, n in area_counts(result).items()]
    total = sum(c[k] for k in ("critical", "high", "medium", "low"))
    if total:
        parts.append(f"It reported {total} finding(s): {c['critical']} critical, {c['high']} high, {c['medium']} medium, "
                     f"{c['low']} low.")
    else:
        parts.append("None of the checks that ran reported a problem.")
    return Summary(status_label=label, status_headline=headline, status_meaning=meaning, overview=" ".join(parts),
                   top_issue=top_issue, strengths=[s.title for s in strengths[:2]], attention_areas=areas,
                   first_action=actions[0].title if actions and actions[0].title != "No urgent actions" else None)


def build_report_data(*, profile: ProjectProfile, result: AnalysisResult, run: RunInfo, project: ProjectInfo,
                      narrative: str | None) -> ReportData:
    texts: dict[str, FindingText] = {}
    for f in [*result.findings]:
        t = explain(f)
        label, meaning = VERIFICATION_TEXT[f.verification]
        loc = (f"`{f.file_path}`" + (f":{f.line}" if f.line else "")) if f.file_path else ""
        texts[f.id] = FindingText(id=f.id, title=t.title, what=t.what, why=t.why, risk=t.risk, fix=t.fix,
                                  context=context_note(f), verification_label=label, verification_meaning=meaning,
                                  location=loc)
    strengths = _strengths(profile, result)
    actions = _actions(result, texts, profile)
    tests = [f.path for f in profile.inventory.files if f.kind == "test"]
    return ReportData(
        run=run, project=project, narrative=redact(narrative) if narrative else None,
        summary=_summary(project, profile, result, strengths, actions, texts), strengths=strengths,
        actions=actions, finding_text=list(texts.values()), frameworks=profile.frameworks,
        package_managers=profile.package_managers, architecture=_architecture(profile),
        testing=TestingInfo(frameworks=profile.test_frameworks, test_file_count=len(tests), test_files=tests[:15]),
        methodology=_methodology(profile, result, project.source), result=result)
