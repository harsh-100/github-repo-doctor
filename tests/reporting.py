"""Helpers for finding what a run published (reports/<project>/runs/<run-id>/...)."""
import json
from pathlib import Path

from repo_doctor.reports.workspace import find_workspace, latest_run


def run_dir(out: Path, root: Path, include_provisional: bool = False) -> Path:
    ws = find_workspace(out, root)
    assert ws is not None, f"no workspace for {root} in {out}"
    meta = latest_run(ws, root, include_provisional=include_provisional)
    assert meta is not None, "no completed run"
    return ws.runs_dir / meta["run_id"]


def report_md(out: Path, root: Path) -> str:
    return (run_dir(out, root) / "report.md").read_text()


def report_data(out: Path, root: Path, include_provisional: bool = False) -> dict:
    return json.loads((run_dir(out, root, include_provisional) / "findings.json").read_text())


def result(out: Path, root: Path, include_provisional: bool = False) -> dict:
    return report_data(out, root, include_provisional)["result"]
