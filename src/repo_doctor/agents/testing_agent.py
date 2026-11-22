"""Testing analysis. Never executes tests (that needs explicit approval, Phase 3)."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import PurePosixPath

from repo_doctor.core.models import Finding, Severity, Verification
from repo_doctor.providers import ProviderError

from .base import JSON_FORMAT, Agent, AgentContext, ask_findings, build_batches

MIN_MODULE_LINES = 15
MAX_UNTESTED_FINDINGS = 10
TRIVIAL_STEMS = {"__init__", "__main__", "conftest", "setup", "index", "main"}

SYSTEM = (
    "You are a test engineer. For the given untested modules, identify the most valuable missing tests "
    "and edge cases (error paths, boundary values, invalid input). Reference a real function/class "
    "definition line as evidence. Use category 'testing/<subtype>'."
)


def configured_test_dirs(ctx: AgentContext) -> list[str]:
    """Extra test locations declared in project config (pytest testpaths)."""
    dirs: list[str] = []
    for m in ctx.profile.manifests:
        if m.endswith("pyproject.toml"):
            try:
                data = tomllib.loads((ctx.root / m).read_text(encoding="utf-8"))
                dirs += data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", [])
            except (OSError, ValueError):
                pass
        if m.endswith("package.json"):
            try:
                cfg = json.loads((ctx.root / m).read_text(encoding="utf-8")).get("jest", {})
                dirs += cfg.get("roots", []) if isinstance(cfg, dict) else []
            except (OSError, ValueError):
                pass
    return [d.strip("./").rstrip("/") for d in dirs if isinstance(d, str)]


class TestingAgent(Agent):
    __test__ = False  # not a pytest class
    name = "testing"
    commands = frozenset({"analyze", "tests"})

    def run(self, ctx: AgentContext) -> list[Finding]:
        inv = ctx.profile.inventory
        extra = configured_test_dirs(ctx)
        tests = [f.path for f in inv.files if f.kind == "test"
                 or any(f.path.startswith(d + "/") for d in extra if d)]
        source = [f for f in inv.files if f.kind == "source" and f.language
                  and PurePosixPath(f.path).stem not in TRIVIAL_STEMS]
        findings: list[Finding] = []
        fw = ", ".join(ctx.profile.test_frameworks) or "none detected"

        if source and not tests:
            findings.append(Finding(
                id="tmp", agent=self.name, category="testing/no-tests", severity=Severity.MEDIUM,
                title="No automated tests found",
                description=f"No test files were found by naming conventions or configured test paths "
                            f"(frameworks: {fw}). {len(source)} source modules are unprotected.",
                evidence=f"{len(inv.files)} files scanned; 0 test files; test config dirs: {extra or 'none'}",
                suggested_fix="Add a test suite (e.g. pytest or Jest) starting with the core modules.",
                confidence=0.8, verification=Verification.VERIFIED))
            return findings

        corpus = "\n".join(ctx.reader.text(t) for t in tests)
        untested: list[str] = []
        for f in sorted(source, key=lambda f: -f.size):
            lines = ctx.reader.lines(f.path)
            if len([x for x in lines if x.strip()]) < MIN_MODULE_LINES:
                continue
            stem = PurePosixPath(f.path).stem
            if not re.search(rf"\b{re.escape(stem)}\b", corpus):
                untested.append(f.path)
        for rel in untested[:MAX_UNTESTED_FINDINGS]:
            findings.append(Finding(
                id="tmp", agent=self.name, category="testing/untested-module", severity=Severity.LOW,
                title=f"No test references module '{PurePosixPath(rel).stem}'",
                description=f"None of the {len(tests)} test files mention this module's name "
                            f"(frameworks: {fw}; test dirs: {extra or 'conventional'}). It may be tested "
                            "indirectly or by unconventional means, so verify before acting.",
                file_path=rel, evidence=f"searched {len(tests)} test files for '{PurePosixPath(rel).stem}'",
                suggested_fix="Add tests covering this module's public behaviour and error paths.",
                confidence=0.5, verification=Verification.SUPPORTED))
        if len(untested) > MAX_UNTESTED_FINDINGS:
            ctx.notes.append(f"testing: {len(untested) - MAX_UNTESTED_FINDINGS} more modules lack test references "
                             "(only the largest are listed).")

        if ctx.llm_enabled and untested:
            batches, _ = build_batches(ctx, untested[:3])
            existing = ", ".join(tests[:15]) or "(none)"
            for body, sent in batches[:1]:
                prompt = (f"Existing test files: {existing}\nTest frameworks: {fw}\n\n"
                          f"Untested modules:\n\n{body}\n\n{JSON_FORMAT}")
                try:
                    findings += ask_findings(ctx, self.name, "testing", SYSTEM, prompt, sent)
                except ProviderError as exc:
                    ctx.warnings.append(f"testing: LLM call failed: {exc}")
        return findings
