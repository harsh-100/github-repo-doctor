"""Detect, approve, run and parse external static-analysis tools."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from repo_doctor.analyzers.repository import DEFAULT_EXCLUDED_DIRS, load_ignore_spec
from repo_doctor.analyzers.tool_parsers import (
    ParseContext,
    ToolOutputError,
    parse_bandit,
    parse_eslint,
    parse_gitleaks,
    parse_npm_audit,
    parse_ruff,
    parse_tsc,
)
from repo_doctor.core.models import Finding, ProjectProfile, ToolRun
from repo_doctor.core.settings import ToolSettings
from repo_doctor.security.permissions import ApprovalPolicy, CommandNotAllowed
from repo_doctor.tools.command_runner import run_command
from repo_doctor.tools.file_reader import RepoReader

Progress = Callable[[str, str], None]

_EXCLUDES = sorted(DEFAULT_EXCLUDED_DIRS)
_ESLINT_CONFIGS = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
                   ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml",
                   ".eslintrc.yaml")


@dataclass
class Resolved:
    path: str
    project_local: bool  # lives inside the repo => executing it runs repository code


@dataclass
class Plan:
    argv: list[str]
    purpose: str
    cleanup: Callable[[], None] | None = None
    read_report: Callable[[], str] | None = None  # for tools that write a report file


@dataclass
class ToolSpec:
    name: str
    exe: str
    areas: frozenset[str]  # {"quality"} / {"security"}
    install_hint: str
    parser: Callable[[str, ParseContext], list[Finding]]
    ok_codes: tuple[int, ...] = (0, 1)
    network: bool = False
    node_tool: bool = False  # may be a project-local node_modules binary


def _has(root: Path, *names: str) -> bool:
    return any((root / n).exists() for n in names)


def _has_ruff_config(root: Path) -> bool:
    if _has(root, "ruff.toml", ".ruff.toml"):
        return True
    py = root / "pyproject.toml"
    return py.exists() and "[tool.ruff" in py.read_text(encoding="utf-8", errors="ignore")


def _has_eslint_config(root: Path) -> bool:
    if _has(root, *_ESLINT_CONFIGS):
        return True
    pkg = root / "package.json"
    return pkg.exists() and '"eslintConfig"' in pkg.read_text(encoding="utf-8", errors="ignore")


def _langs(profile: ProjectProfile) -> set[str]:
    return set(profile.languages)


# ---- per-tool: applicability (reason if not applicable) and command plans
def _na_ruff(root, profile):
    return None if "Python" in _langs(profile) else "no Python files"


def _plan_ruff(root, exe, profile):
    argv = [exe, "check", "--no-fix", "--no-cache", "--output-format", "json",
            "--extend-exclude", ",".join(_EXCLUDES)]
    if not _has_ruff_config(root):
        argv += ["--select", "E,F,B", "--ignore", "E501"]  # modest defaults when the project has no Ruff config
    return Plan(argv + ["."], "lint Python code (read-only, no auto-fix)")


def _na_bandit(root, profile):
    return None if "Python" in _langs(profile) else "no Python files"


def _plan_bandit(root, exe, profile):
    ex = ",".join(f"./{d}" for d in _EXCLUDES)
    return Plan([exe, "-r", ".", "-f", "json", "-q", "-x", ex, "--skip", "B101"],
                "scan Python code for security issues")


def _na_gitleaks(root, profile):
    return None


def _plan_gitleaks(root, exe, profile):
    fd, report = tempfile.mkstemp(prefix="repo-doctor-gitleaks-", suffix=".json")
    os.close(fd)

    def read() -> str:
        return Path(report).read_text(encoding="utf-8", errors="replace")

    def cleanup() -> None:
        Path(report).unlink(missing_ok=True)

    argv = [exe, "detect", "--no-git", "--source", ".", "--redact", "--no-banner",
            "--report-format", "json", "--report-path", report, "--exit-code", "1"]
    return Plan(argv, "scan for committed secrets (values are redacted by gitleaks)", cleanup, read)


def _na_eslint(root, profile):
    if not _langs(profile) & {"JavaScript", "TypeScript"}:
        return "no JavaScript/TypeScript files"
    return None if _has_eslint_config(root) else "no ESLint configuration found in the project"


def _plan_eslint(root, exe, profile):
    ignores = [a for d in _EXCLUDES for a in ("--ignore-pattern", f"**/{d}/**")]
    return Plan([exe, ".", "-f", "json", "--no-fix", *ignores], "lint JavaScript/TypeScript with the project's ESLint config")


def _na_tsc(root, profile):
    if "TypeScript" not in _langs(profile):
        return "no TypeScript files"
    return None if (root / "tsconfig.json").exists() else "no tsconfig.json"


def _plan_tsc(root, exe, profile):
    return Plan([exe, "--noEmit", "--pretty", "false", "-p", "tsconfig.json"], "type-check TypeScript (no output files)")


def _na_npm(root, profile):
    if not (root / "package.json").exists():
        return "no package.json"
    return None if _has(root, "package-lock.json", "npm-shrinkwrap.json") else "no package-lock.json (npm audit needs a lockfile)"


def _plan_npm(root, exe, profile):
    return Plan([exe, "audit", "--json"], "check npm dependencies against the advisory database")


SPECS: list[tuple[ToolSpec, Callable, Callable]] = [
    (ToolSpec("ruff", "ruff", frozenset({"quality"}), "pip install ruff", parse_ruff), _na_ruff, _plan_ruff),
    (ToolSpec("eslint", "eslint", frozenset({"quality"}), "npm install --save-dev eslint (in the project)",
              parse_eslint, node_tool=True), _na_eslint, _plan_eslint),
    (ToolSpec("tsc", "tsc", frozenset({"quality"}), "npm install --save-dev typescript (in the project)",
              parse_tsc, ok_codes=(0, 1, 2), node_tool=True), _na_tsc, _plan_tsc),
    (ToolSpec("bandit", "bandit", frozenset({"security"}), "pip install bandit", parse_bandit), _na_bandit, _plan_bandit),
    (ToolSpec("gitleaks", "gitleaks", frozenset({"security"}),
              "https://github.com/gitleaks/gitleaks#installing", parse_gitleaks), _na_gitleaks, _plan_gitleaks),
    (ToolSpec("npm-audit", "npm", frozenset({"security"}), "install Node.js (https://nodejs.org)",
              parse_npm_audit, network=True), _na_npm, _plan_npm),
]
TOOL_NAMES = [s.name for s, _, _ in SPECS]
MODE_AREAS = {"analyze": {"quality", "security"}, "quality": {"quality"}, "security": {"security"}, "tests": set()}


def resolve_executable(spec: ToolSpec, root: Path) -> Resolved | None:
    names = [spec.exe] + ([spec.exe + ".cmd"] if sys.platform == "win32" else [])
    if spec.node_tool:
        for n in names:
            local = root / "node_modules" / ".bin" / n
            if local.is_file():
                return Resolved(str(local), True)
    found = shutil.which(spec.exe)
    if found:
        return Resolved(found, False)
    for n in names:  # tools installed next to this interpreter (e.g. the venv) but PATH not activated
        cand = Path(sys.executable).parent / n
        if cand.is_file():
            return Resolved(str(cand), False)
    return None


def detect_tools(root: Path | None = None) -> dict[str, Resolved | None]:
    r = root or Path(".")
    return {s.name: resolve_executable(s, r) for s, _, _ in SPECS}


class ToolSuite:
    def __init__(self, policy: ApprovalPolicy, settings: ToolSettings | None = None):
        self.policy = policy
        self.settings = settings or ToolSettings()

    def selected(self, mode: str, only: set[str] | None = None) -> list[tuple[ToolSpec, Callable, Callable]]:
        areas = MODE_AREAS.get(mode, set())
        return [t for t in SPECS if t[0].areas & areas and t[0].name not in self.settings.disabled
                and (only is None or t[0].name in only)]

    def run(self, mode: str, root: Path, profile: ProjectProfile, reader: RepoReader,
            progress: Progress | None = None,
            only: set[str] | None = None) -> tuple[list[Finding], list[ToolRun], list[str]]:
        findings: list[Finding] = []
        runs: list[ToolRun] = []
        notes: list[str] = []
        root = root.resolve()
        ignore = load_ignore_spec(root, [])
        for spec, not_applicable, plan_fn in self.selected(mode, only):
            reason = not_applicable(root, profile)
            if reason:
                runs.append(ToolRun(name=spec.name, status="skipped", detail=f"not applicable: {reason}"))
                continue
            exe = resolve_executable(spec, root)
            if exe is None:
                runs.append(ToolRun(name=spec.name, status="skipped",
                                    detail=f"not installed. Install: {spec.install_hint}"))
                continue
            plan = plan_fn(root, exe.path, profile)
            cmd_str = " ".join(plan.argv)
            try:
                if not self.policy.approve(plan.argv, plan.purpose, spec.network, exe.project_local):
                    runs.append(ToolRun(name=spec.name, status="denied", command=cmd_str,
                                        detail="not approved (use --trust-tools or answer yes at the prompt)"))
                    continue
                if progress:
                    progress(spec.name, "running")
                res = run_command(plan.argv, root, self.settings.timeout_seconds)
                raw = plan.read_report() if (plan.read_report and not res.timed_out) else res.stdout
                pctx = ParseContext(root, reader, ignore, set(DEFAULT_EXCLUDED_DIRS))
                if res.timed_out:
                    runs.append(ToolRun(name=spec.name, status="failed", command=cmd_str,
                                        detail=f"timed out after {self.settings.timeout_seconds}s"))
                elif res.returncode not in spec.ok_codes:
                    runs.append(ToolRun(name=spec.name, status="failed", command=cmd_str,
                                        detail=f"exit code {res.returncode}: {res.stderr.strip()[:300] or 'no error output'}"))
                else:
                    found = spec.parser(raw, pctx)
                    findings += found
                    detail = f"{len(found)} finding(s)"
                    if pctx.omitted:
                        detail += f" (+{pctx.omitted} lower-severity omitted by the per-tool cap)"
                        notes.append(f"{spec.name}: {pctx.omitted} additional finding(s) omitted by the cap.")
                    runs.append(ToolRun(name=spec.name, status="ran", command=cmd_str, findings=len(found), detail=detail))
            except (CommandNotAllowed, ToolOutputError, OSError) as exc:
                runs.append(ToolRun(name=spec.name, status="failed", command=cmd_str, detail=str(exc)[:300]))
            finally:
                if plan.cleanup:
                    plan.cleanup()
        return findings, runs, notes
