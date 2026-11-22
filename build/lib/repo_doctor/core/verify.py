"""Post-fix verification. A fix is rolled back if ANY check reports `fail`.

Checks (each: pass | fail | inconclusive | skipped):
  syntax            the edited file still parses (Python / JSON)
  builtin-detectors same built-in scans as the analysis, before vs after: the targeted issue must
                    be gone (if a built-in check found it) and the file must not gain new issues
  tool:<name>       the originating tool re-run (needs approval); same before/after comparison
  tests             the project's tests (only with --run-tests + approval); must not regress
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from repo_doctor.agents.code_agent import python_checks
from repo_doctor.agents.security_agent import scan_file
from repo_doctor.analyzers.language import classify, detect_language
from repo_doctor.analyzers.project import build_profile
from repo_doctor.analyzers.repository import scan_repository
from repo_doctor.analyzers.static_analysis import ToolSuite
from repo_doctor.core.fixer import syntax_error
from repo_doctor.core.models import Finding, Verification
from repo_doctor.core.settings import Settings
from repo_doctor.security.permissions import ApprovalPolicy, CommandNotAllowed
from repo_doctor.tools.command_runner import run_command
from repo_doctor.tools.file_reader import RepoReader

Key = tuple[str, str]  # (category, title)


def _check(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail}


def builtin_counts(rel: str, text: str) -> Counter[Key]:
    lines = text.split("\n")
    lang, kind = detect_language(rel), classify(rel)
    found = python_checks(rel, lines) if lang == "Python" else []
    found += scan_file(rel, lang, kind, lines)
    return Counter((f.category, f.title) for f in found)


class Verifier:
    def __init__(self, root: Path, settings: Settings, policy: ApprovalPolicy, suite: ToolSuite | None = None,
                 run_tests: bool = False):
        self.root = root.resolve()
        self.settings = settings
        self.policy = policy
        self.suite = suite
        self.run_tests = run_tests
        self._before_builtin: dict[str, Counter[Key]] = {}
        self._before_tool: dict[tuple[str, str], Counter[str] | None] = {}
        self._tests_ok: dict[str, bool | None] = {}
        self._notes: list[str] = []
        self._py_tests: bool | None = None

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _tool_name(f: Finding) -> str | None:
        for part in f.agent.split(", "):
            if part.startswith("tool:"):
                return part[5:]
        return None

    def _tool_counts(self, tool: str, rel: str) -> Counter[str] | None:
        """Findings per category for `rel` from a fresh run of `tool`; None if it could not run."""
        if not self.suite:
            return None
        inv = scan_repository(self.root, self.settings.scan)
        profile = build_profile(self.root, inv)
        findings, runs, notes = self.suite.run("analyze", self.root, profile, RepoReader(self.root, inv), only={tool})
        if not runs or runs[0].status != "ran" or notes:  # failed, denied, skipped or capped => not trustworthy
            self._notes.append(f"{tool}: {runs[0].detail if runs else 'did not run'}")
            return None
        return Counter(f.category for f in findings if f.file_path == rel)

    def _find_pytest(self) -> str | None:
        """First working pytest: active virtualenv, then PATH, then next to this interpreter."""
        import shutil
        import sys
        cands = []
        if os.environ.get("VIRTUAL_ENV"):
            cands.append(str(Path(os.environ["VIRTUAL_ENV"]) / "bin" / "pytest"))
        cands += [shutil.which("pytest") or "", str(Path(sys.executable).parent / "pytest")]
        for c in dict.fromkeys(x for x in cands if x and Path(x).exists()):
            try:
                if run_command([c, "--version"], self.root, 30).returncode == 0:
                    return c
            except (CommandNotAllowed, OSError):
                continue
        return None

    def _has_python_tests(self) -> bool:
        if self._py_tests is None:
            inv = scan_repository(self.root, self.settings.scan)
            self._py_tests = any(f.kind == "test" and f.language == "Python" for f in inv.files)
        return self._py_tests

    def _test_command(self, rel: str) -> tuple[list[str], str] | None:
        import shutil
        if rel.endswith(".py") and self._has_python_tests():
            exe = self._find_pytest()
            if exe:
                return [exe, "-x", "-q", "-p", "no:cacheprovider"], "run the project's Python tests"
        pkg = self.root / "package.json"
        if rel.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")) and pkg.exists():
            try:
                script = (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}).get("test", "")
            except ValueError:
                return None
            if script and "no test specified" not in script and shutil.which("npm"):
                return [shutil.which("npm") or "npm", "test", "--silent"], "run the project's npm test script"
        return None

    def _run_tests(self, rel: str) -> bool | None:
        """True = pass, False = fail, None = could not run / denied / inconclusive."""
        cmd = self._test_command(rel)
        if cmd is None:
            return None
        argv, purpose = cmd
        if not self.policy.approve(argv, purpose, network=False, runs_repo_code=True):
            return None
        try:
            res = run_command(argv, self.root, self.settings.tools.timeout_seconds * 3)
        except (CommandNotAllowed, OSError):
            return None
        if res.timed_out or res.returncode == 5:  # pytest: no tests collected
            return None
        return res.returncode == 0

    # ---------------------------------------------------------------- protocol
    def prepare(self, f: Finding, rel: str, old_text: str) -> None:
        self._before_builtin[rel] = builtin_counts(rel, old_text)
        tool = self._tool_name(f)
        if tool and (tool, rel) not in self._before_tool:
            self._before_tool[(tool, rel)] = self._tool_counts(tool, rel)
        if self.run_tests and rel not in self._tests_ok:
            self._tests_ok[rel] = self._run_tests(rel)

    def check(self, f: Finding, rel: str, new_text: str) -> tuple[bool, list[dict]]:
        checks: list[dict] = []

        err = syntax_error(rel, new_text)
        if rel.endswith((".py", ".json")):
            checks.append(_check("syntax", "fail", err) if err else _check("syntax", "pass", "file still parses"))
        else:
            checks.append(_check("syntax", "skipped", "no syntax checker for this file type"))

        before, after = self._before_builtin.get(rel, Counter()), builtin_counts(rel, new_text)
        key: Key = (f.category, f.title)
        builtin_target = f.verification == Verification.VERIFIED and not self._tool_name(f) and before[key] > 0
        new_issues = sum((after - before).values())
        if builtin_target and after[key] >= before[key]:
            checks.append(_check("builtin-detectors", "fail", f"'{f.title}' is still detected"))
        elif new_issues:
            added = ", ".join(t for (_, t) in (after - before))
            checks.append(_check("builtin-detectors", "fail", f"fix introduced {new_issues} new issue(s): {added}"))
        elif builtin_target:
            checks.append(_check("builtin-detectors", "pass", f"'{f.title}' no longer detected; no new issues"))
        else:
            checks.append(_check("builtin-detectors", "pass", "no new issues introduced"))

        tool = self._tool_name(f)
        if tool:
            tb = self._before_tool.get((tool, rel))
            ta = self._tool_counts(tool, rel) if tb is not None else None
            name = f"tool:{tool}"
            if tb is None or ta is None:
                checks.append(_check(name, "inconclusive", "tool could not be re-run (not approved/unavailable/failed)"))
            else:
                self._before_tool[(tool, rel)] = ta
                if ta[f.category] >= tb[f.category]:
                    checks.append(_check(name, "fail", f"{tool} still reports this issue ({f.category})"))
                elif sum((ta - tb).values()):
                    checks.append(_check(name, "fail", f"{tool} reports new issue(s) after the fix"))
                else:
                    checks.append(_check(name, "pass", f"{tool} no longer reports it; no new issues"))

        if self.run_tests:
            base = self._tests_ok.get(rel)
            if base is not True:
                checks.append(_check("tests", "inconclusive",
                                     "tests unavailable, not approved, or not passing before the change"))
            else:
                now = self._run_tests(rel)
                checks.append(_check("tests", "pass", "project tests still pass") if now is True
                              else _check("tests", "fail", "project tests fail after the change") if now is False
                              else _check("tests", "inconclusive", "tests could not be re-run"))

        return all(c["status"] != "fail" for c in checks), checks

    def discard(self, f: Finding, rel: str) -> None:
        """After a rollback the 'before' state of this file is stale; recompute next time."""
        self._before_builtin.pop(rel, None)
        tool = self._tool_name(f)
        if tool:
            self._before_tool.pop((tool, rel), None)
