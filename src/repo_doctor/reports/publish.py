"""Publish one analysis run into the project's report workspace. Never overwrites an earlier run."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from repo_doctor import __version__
from repo_doctor.core.models import AnalysisResult, ProjectProfile
from repo_doctor.reports import charts
from repo_doctor.reports.data import ProjectInfo, ReportData, RunInfo, build_report_data
from repo_doctor.reports.html import render_dashboard, render_html
from repo_doctor.reports.markdown import render_markdown
from repo_doctor.reports.workspace import (
    SCHEMA_VERSION,
    Workspace,
    atomic_write_text,
    find_or_create_workspace,
    list_runs,
    new_run_dir,
    root_hash,
    update_project_files,
)


@dataclass
class PublishedRun:
    workspace: Workspace
    run_id: str
    run_dir: Path
    markdown: Path
    html: Path
    findings: Path
    metadata: Path
    dashboard: Path
    previous_runs: int


def _metadata(data: ReportData, run_dir_name: str) -> dict:
    r = data.result
    c = r.severity_counts()
    return {
        "schema_version": SCHEMA_VERSION, "run_id": data.run.id, "created_at": data.run.created_at,
        "duration_seconds": round(data.run.duration_seconds, 2), "mode": data.run.mode,
        "tool_version": data.run.tool_version, "project": {"name": data.project.name, "slug": data.project.slug},
        "root_hash": data.project.root_hash,
        "source": ({k: data.project.source.get(k) for k in ("kind", "url", "owner", "repo", "branch", "commit")}
                   if data.project.source else None), "health": r.health(), "counts": c,
        "verification": data.verification_counts(), "total_findings": sum(c[k] for k in ("critical", "high", "medium", "low")),
        "rejected": len(r.rejected), "agents_run": data.methodology.agents,
        "tools": [{"name": t["name"], "status": t["status"], "findings": t.get("findings", 0)} for t in data.methodology.tools],
        "llm": {"used": data.run.llm_used, "calls": data.run.llm_calls, "provider": data.run.provider, "model": data.run.model},
        "provisional": data.run.provisional,
        "files": {"markdown": "report.md", "html": "report.html", "findings": "findings.json"},
    }


def refresh_project(ws: Workspace) -> Path:
    runs = list_runs(ws)
    update_project_files(ws, render_dashboard(ws.name, ws.remote, runs))
    return ws.path / "index.html"


def publish_run(*, reports_root: Path, project_root: Path, profile: ProjectProfile, result: AnalysisResult,
                narrative: str | None, mode: str, started: datetime, provider: str | None,
                model: str | None, provisional: bool = False, source: dict | None = None) -> PublishedRun:
    """`source` (e.g. a GitHub repo analysed from a temp clone) gives the project its name and stable identity."""
    project_root = project_root.resolve()
    remote_name = source["repo"] if source else None
    identity = (f"git:github.com/{source['owner'].lower()}/{source['repo'].lower()}", f"github.com/{source['owner']}/{source['repo']}") if source else None
    ws = find_or_create_workspace(reports_root, project_root, name=remote_name, identity=identity)
    previous = list_runs(ws)
    run_id, run_dir = new_run_dir(ws)
    now = datetime.now(UTC)
    info = ProjectInfo(name=ws.name if source else project_root.name, slug=ws.slug,
                       root="" if source else str(project_root), root_hash="" if source else root_hash(project_root),
                       identity_hash=ws.identity_hash, remote=ws.remote, source=source)
    run = RunInfo(id=run_id, created_at=now.isoformat(timespec="microseconds"),
                  duration_seconds=max(0.0, (now - started).total_seconds()), mode=mode, tool_version=__version__,
                  provider=provider, model=model, llm_used=result.llm_used, llm_calls=result.llm_calls,
                  provisional=provisional)
    data = build_report_data(profile=profile, result=result, run=run, project=info, narrative=narrative)

    c, vc, ac = result.severity_counts(), data.verification_counts(), data.area_counts()
    for name, svg in (("severity.svg", charts.severity_chart(c, True)), ("verification.svg", charts.verification_chart(vc, True)),
                      ("area.svg", charts.area_chart(ac, True))):
        if svg:
            atomic_write_text(run_dir / "assets" / name, svg)
    findings = run_dir / "findings.json"
    atomic_write_text(findings, data.model_dump_json(indent=2) + "\n")        # the source of truth
    md, html = run_dir / "report.md", run_dir / "report.html"
    atomic_write_text(md, render_markdown(data))
    atomic_write_text(html, render_html(data, previous))
    meta = run_dir / "metadata.json"
    atomic_write_text(meta, json.dumps(_metadata(data, run_dir.name), indent=2) + "\n")  # last: marks the run complete
    index = refresh_project(ws)
    return PublishedRun(ws, run_id, run_dir, md, html, findings, meta, index, len(previous))
