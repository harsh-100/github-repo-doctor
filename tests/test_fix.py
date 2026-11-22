import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fakes import FakeProvider

from repo_doctor.agents.code_agent import python_checks
from repo_doctor.analyzers.static_analysis import ToolSuite
from repo_doctor.core.fixer import (
    Backups,
    Hooks,
    Skip,
    atomic_write,
    propose_fix,
    run_fix_session,
    select_findings,
    unfixable_reason,
)
from repo_doctor.core.models import AnalysisResult, Finding, Severity, Verification
from repo_doctor.core.settings import Settings, ToolSettings
from repo_doctor.core.verify import Verifier
from repo_doctor.providers import ProviderError
from repo_doctor.reports.fix_report import render_fix_report
from repo_doctor.security.permissions import ApprovalPolicy

CODE = "def load(path):\n    try:\n        return open(path).read()\n    except:\n        return None\n"
EDIT = {"search": "    except:\n        return None", "replace": "    except OSError:\n        return None",
        "reason": "catch only file errors"}


def reply(*edits, **extra):
    return FakeProvider({"edits": list(edits), **extra})


def bare_finding(**kw):
    base = {"id": "F-001", "agent": "code", "category": "quality/bare-except", "severity": Severity.LOW,
            "title": "Bare except", "description": "hides bugs", "file_path": "mod.py", "line": 4,
            "evidence": "except:", "suggested_fix": "catch specific exceptions", "confidence": 0.9,
            "verification": Verification.VERIFIED}
    return Finding(**{**base, **kw})


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "mod.py").write_text(CODE)
    return root


@pytest.fixture
def backup_base(tmp_path):
    return tmp_path / "backups"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------- eligibility
@pytest.mark.parametrize("kw,expect", [
    ({"file_path": None}, "project-level"),
    ({"line": None}, "no line"),
    ({"verification": Verification.SPECULATIVE}, "speculative"),
    ({"severity": Severity.INFO}, "informational"),
    ({"category": "security/hardcoded-secret"}, "credential"),
    ({"category": "security/gitleaks-aws"}, "credential"),
    ({"category": "security/npm-audit-lodash"}, "dependency"),
    ({"category": "testing/untested-module"}, "test files"),
    ({"file_path": ".env"}, "sensitive"),
    ({"evidence": 'TOKEN = "[REDACTED]"'}, "secret-like"),
])
def test_unfixable_reasons(kw, expect):
    assert expect in unfixable_reason(bare_finding(**kw))


def test_fixable_finding_has_no_reason():
    assert unfixable_reason(bare_finding()) is None


def test_select_findings_filters_by_id_and_severity():
    r = AnalysisResult(mode="analyze", findings=[
        bare_finding(id="F-001", severity=Severity.HIGH), bare_finding(id="F-002", line=9),
        bare_finding(id="F-003", category="security/hardcoded-secret")])
    fixable, unfixable = select_findings(r, [])
    assert [f.id for f in fixable] == ["F-001", "F-002"] and [f.id for f, _ in unfixable] == ["F-003"]
    assert [f.id for f in select_findings(r, ["f-002"])[0]] == ["F-002"]
    assert [f.id for f in select_findings(r, [], Severity.HIGH)[0]] == ["F-001"]


# ---------------------------------------------------------------- proposal validation
def test_valid_proposal_builds_diff_and_does_not_touch_file(proj):
    prop = propose_fix(reply(EDIT), proj, bare_finding())
    assert "-    except:" in prop.diff and "+    except OSError:" in prop.diff
    assert (proj / "mod.py").read_text() == CODE  # proposing never writes
    assert prop.new_text.count("except OSError") == 1


@pytest.mark.parametrize("edit,why", [
    ({"search": "    except:", "replace": "    except OSError:"}, None),  # control: valid
    ({"search": "nonexistent line", "replace": "x"}, "matched 0 times"),
    ({"search": "return", "replace": "x"}, "matched 2 times"),
    ({"search": "   ", "replace": "x"}, "empty search"),
    ({"search": "    except:", "replace": "    except:"}, "no change"),
    ({"search": "    except:", "replace": "    except [REDACTED]:"}, "redacted"),
    ({"search": "    except:", "replace": "    except OSError:\n" + "        pass\n" * 70}, "too large"),
    ({"search": "    except:", "replace": "    except OSError as e:\n        key = 'sk-ant-abcdefghijklmnop12345'"},
     "secret-like"),
    ({"search": "    except:", "replace": "    except (:"}, "break syntax"),
])
def test_edit_validation(proj, edit, why):
    if why is None:
        propose_fix(reply(edit), proj, bare_finding())
        return
    with pytest.raises(Skip, match=why):
        propose_fix(reply(edit), proj, bare_finding())


def test_edit_outside_shown_window_is_rejected(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    body = "".join(f"x{i} = {i}\n" for i in range(200)) + CODE
    (root / "mod.py").write_text(body)
    f = bare_finding(line=204)
    far = {"search": "x0 = 0", "replace": "x0 = 1"}
    with pytest.raises(Skip, match="outside the code"):
        propose_fix(reply(far), root, f)


def test_too_many_edits_and_overlap_and_no_edits(proj):
    with pytest.raises(Skip, match="max 3"):
        propose_fix(reply(*[EDIT] * 4), proj, bare_finding())
    a = {"search": "    except:\n        return None", "replace": "    except OSError:\n        return None"}
    b = {"search": "        return None", "replace": "        return ''"}
    with pytest.raises(Skip, match="overlap"):
        propose_fix(reply(a, b), proj, bare_finding())
    with pytest.raises(Skip, match="no automatic fix: too risky"):
        propose_fix(reply(cannot_fix="too risky"), proj, bare_finding())


def test_malformed_model_output_and_provider_failure(proj):
    with pytest.raises(Skip, match="valid JSON"):
        propose_fix(FakeProvider("nope", "still nope"), proj, bare_finding())
    ok = FakeProvider("nope", json_reply := {"edits": [EDIT]})
    assert propose_fix(ok, proj, bare_finding()).new_text and json_reply
    with pytest.raises(Skip, match="malformed"):
        propose_fix(FakeProvider({"edits": [{"search": "x"}]}), proj, bare_finding())
    with pytest.raises(Skip, match="request failed"):
        propose_fix(FakeProvider(ProviderError("down")), proj, bare_finding())


def test_stale_finding_is_refused(proj):
    (proj / "mod.py").write_text("def load(path):\n    return 1\n")
    with pytest.raises(Skip, match="no longer found"):
        propose_fix(reply(EDIT), proj, bare_finding())


def test_line_drift_is_relocated_from_evidence(proj):
    (proj / "mod.py").write_text("# header\n# more\n" + CODE)
    prop = propose_fix(reply(EDIT), proj, bare_finding(line=4))
    assert "except OSError" in prop.new_text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks/permissions")
def test_symlink_and_non_utf8_and_missing_files_refused(proj, tmp_path):
    (tmp_path / "real.py").write_text(CODE)
    (proj / "link.py").symlink_to(tmp_path / "real.py")
    with pytest.raises(Skip, match="symlink|escapes"):
        propose_fix(reply(EDIT), proj, bare_finding(file_path="link.py"))
    (proj / "bin.py").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(Skip, match="UTF-8"):
        propose_fix(reply(EDIT), proj, bare_finding(file_path="bin.py", evidence=""))
    with pytest.raises(Skip, match="no longer exists"):
        propose_fix(reply(EDIT), proj, bare_finding(file_path="gone.py"))
    with pytest.raises(Skip, match="escapes"):
        propose_fix(reply(EDIT), proj, bare_finding(file_path="../outside.py"))


def test_model_only_sees_redacted_code(proj):
    (proj / "mod.py").write_text('API_KEY = "sk-ant-abcdefghijklmnop1234567890"\npassword = "hunter2secret"\n' + CODE)
    p = reply(EDIT)
    propose_fix(p, proj, bare_finding(line=6))
    sent = p.prompts[0]
    assert "hunter2secret" not in sent and "sk-ant-abcdefghijklmnop" not in sent and "[REDACTED]" in sent


def test_diff_context_never_leaks_secrets(proj):
    (proj / "mod.py").write_text('password = "hunter2secret"\n' + CODE)
    prop = propose_fix(reply(EDIT), proj, bare_finding(line=5))
    assert "hunter2secret" not in prop.diff and "hunter2secret" in prop.old_text


def test_crlf_files_keep_their_line_endings(proj):
    (proj / "mod.py").write_bytes(CODE.replace("\n", "\r\n").encode())
    prop = propose_fix(reply(EDIT), proj, bare_finding())
    assert "\r\n" in prop.new_text and "\n" not in prop.new_text.replace("\r\n", "")


# ---------------------------------------------------------------- applying, backups, approval
def hooks(answer=True, log=None):
    return Hooks(approve=lambda p: (log.append(p.rel) if log is not None else None) or answer)


def test_declined_fix_changes_nothing_and_creates_no_backup(proj, backup_base):
    s = run_fix_session(proj, reply(EDIT), [bare_finding()], hooks(False), backup_base=backup_base)
    assert s.records[0].outcome == "declined" and (proj / "mod.py").read_text() == CODE
    assert not backup_base.exists()


def test_dry_run_never_asks_or_writes(proj, backup_base):
    asked = []
    shown = []
    h = Hooks(approve=lambda p: asked.append(1) or True, show=lambda p: shown.append(p.rel))
    s = run_fix_session(proj, reply(EDIT), [bare_finding()], h, dry_run=True, backup_base=backup_base)
    assert s.records[0].outcome == "dry-run" and asked == [] and shown == ["mod.py"]
    assert (proj / "mod.py").read_text() == CODE and not backup_base.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks/permissions")
def test_approved_fix_is_applied_with_backup_of_original(proj, backup_base):
    mode = 0o640
    os.chmod(proj / "mod.py", mode)
    orig_hash = sha(proj / "mod.py")
    s = run_fix_session(proj, reply(EDIT), [bare_finding()], hooks(True), backup_base=backup_base)
    assert s.records[0].outcome == "applied-unverified"  # no verifier => nothing conclusive
    assert "except OSError" in (proj / "mod.py").read_text()
    assert sha(Path(s.backup_dir) / "mod.py") == orig_hash  # original preserved byte-for-byte
    assert stat.S_IMODE((proj / "mod.py").stat().st_mode) == mode  # permissions kept
    assert not list(proj.glob(".repo-doctor-*"))  # no temp leftovers
    assert not str(backup_base).startswith(str(proj))  # backups live outside the repo


def test_nothing_applied_without_approval_for_that_proposal(proj, backup_base):
    f2 = bare_finding(id="F-002", title="Other", category="quality/other", line=3, evidence="open(path)")
    seen = []
    p = FakeProvider({"edits": [EDIT]}, {"edits": [{"search": "open(path).read()", "replace": "open(path, encoding='utf-8').read()"}]})
    s = run_fix_session(proj, p, [bare_finding(), f2], Hooks(approve=lambda pr: seen.append(pr.finding.id) or pr.finding.id == "F-002"),
                        backup_base=backup_base)
    assert [r.outcome for r in s.records] == ["declined", "applied-unverified"]
    text = (proj / "mod.py").read_text()
    assert "except:" in text and "encoding='utf-8'" in text  # only the approved change landed


def test_sequential_fixes_in_same_file_and_single_backup(proj, backup_base):
    f2 = bare_finding(id="F-002", title="Other", category="quality/other", line=3, evidence="open(path)")
    p = FakeProvider({"edits": [EDIT]}, {"edits": [{"search": "open(path).read()", "replace": "open(path, encoding='utf-8').read()"}]})
    s = run_fix_session(proj, p, [bare_finding(), f2], hooks(True), backup_base=backup_base)
    assert [r.outcome for r in s.records] == ["applied-unverified"] * 2
    assert (Path(s.backup_dir) / "mod.py").read_text() == CODE  # backup is the TRUE original
    final = (proj / "mod.py").read_text()
    assert "except OSError" in final and "encoding='utf-8'" in final


def test_file_changed_while_waiting_for_approval_is_not_applied(proj, backup_base):
    def approve(p):
        (proj / "mod.py").write_text(CODE + "# edited meanwhile\n")
        return True
    s = run_fix_session(proj, reply(EDIT), [bare_finding()], Hooks(approve=approve), backup_base=backup_base)
    assert s.records[0].outcome == "skipped" and "changed while waiting" in s.records[0].detail
    assert (proj / "mod.py").read_text().endswith("# edited meanwhile\n") and "except:" in (proj / "mod.py").read_text()


class FailingVerifier:
    def __init__(self, mode="fail"):
        self.mode, self.discarded = mode, []

    def prepare(self, f, rel, old):
        pass

    def check(self, f, rel, new):
        if self.mode == "crash":
            raise RuntimeError("boom")
        return False, [{"name": "syntax", "status": "fail", "detail": "nope"}]

    def discard(self, f, rel):
        self.discarded.append(rel)


@pytest.mark.parametrize("mode", ["fail", "crash"])
def test_failed_verification_rolls_back_exactly(proj, backup_base, mode):
    v = FailingVerifier(mode)
    s = run_fix_session(proj, reply(EDIT), [bare_finding()], hooks(True), verifier=v, backup_base=backup_base)
    assert s.records[0].outcome == "rolled-back" and (proj / "mod.py").read_text() == CODE
    assert v.discarded == ["mod.py"] and "rolled back" in s.records[0].detail


def test_unfixable_findings_are_skipped_with_reason_and_model_not_called(proj, backup_base):
    p = reply(EDIT)
    s = run_fix_session(proj, p, [bare_finding(category="security/hardcoded-secret")], hooks(True), backup_base=backup_base)
    assert s.records[0].outcome == "skipped" and p.prompts == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks/permissions")
def test_atomic_write_refuses_symlink_and_leaves_no_tmp(proj, tmp_path):
    (tmp_path / "t.py").write_text("x")
    (proj / "ln.py").symlink_to(tmp_path / "t.py")
    from repo_doctor.security.permissions import PathSecurityError
    with pytest.raises(PathSecurityError):
        atomic_write(proj, "ln.py", "evil")
    assert (tmp_path / "t.py").read_text() == "x" and not list(proj.glob(".repo-doctor-*"))


def test_backups_restore_roundtrip(proj, backup_base):
    b = Backups(proj, backup_base)
    b.save("mod.py", CODE)
    b.save("mod.py", "SHOULD NOT OVERWRITE")  # first save wins
    atomic_write(proj, "mod.py", "changed")
    b.restore_to("mod.py", b.original("mod.py"))
    assert (proj / "mod.py").read_text() == CODE


# ---------------------------------------------------------------- verifier (real detectors)
def make_verifier(proj, suite=None, run_tests=False, trusted=True):
    return Verifier(proj, Settings(), ApprovalPolicy(trusted=trusted), suite, run_tests)


def test_verifier_passes_when_issue_resolved(proj, backup_base):
    v = make_verifier(proj)
    s = run_fix_session(proj, reply(EDIT), [bare_finding()], hooks(True), verifier=v, backup_base=backup_base)
    rec = s.records[0]
    assert rec.outcome == "applied" and {c["name"]: c["status"] for c in rec.checks}["builtin-detectors"] == "pass"
    assert "bare except" not in {f.title.lower() for f in python_checks("mod.py", (proj / "mod.py").read_text().split("\n"))}


def test_verifier_rolls_back_when_issue_still_present(proj, backup_base):
    noop = {"search": "        return None", "replace": "        return ''"}  # leaves the bare except
    s = run_fix_session(proj, reply(noop), [bare_finding()], hooks(True), verifier=make_verifier(proj), backup_base=backup_base)
    assert s.records[0].outcome == "rolled-back" and "still detected" in s.records[0].detail
    assert (proj / "mod.py").read_text() == CODE


def test_verifier_rolls_back_when_fix_introduces_new_issue(proj, backup_base):
    bad = {"search": "def load(path):", "replace": "def load(path, cache=[]):"}  # mutable default
    f = bare_finding(line=1, evidence="def load(path):", category="quality/other", title="Something else",
                     verification=Verification.SUPPORTED)
    s = run_fix_session(proj, reply(bad), [f], hooks(True), verifier=make_verifier(proj), backup_base=backup_base)
    assert s.records[0].outcome == "rolled-back" and "new issue" in s.records[0].detail
    assert (proj / "mod.py").read_text() == CODE


def test_verifier_syntax_gate_after_write(proj):
    v = make_verifier(proj)
    ok, checks = v.check(bare_finding(), "mod.py", "def broken(:\n")
    assert not ok and checks[0] == {"name": "syntax", "status": "fail", "detail": checks[0]["detail"]}


RUFF = Path(sys.executable).parent / "ruff"


@pytest.mark.skipif(not RUFF.exists(), reason="ruff not installed")
def test_tool_finding_verified_by_rerunning_the_tool(tmp_path, backup_base):
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("import os\nimport sys\n\nprint(sys.argv)\n")
    f = Finding(id="F-001", agent="tool:ruff", category="quality/ruff-f401", severity=Severity.LOW,
                title="F401: `os` imported but unused", description="d", file_path="m.py", line=1,
                evidence="import os", confidence=0.95, verification=Verification.VERIFIED)
    fix = {"search": "import os\n", "replace": "", "reason": "remove unused import"}
    suite = ToolSuite(ApprovalPolicy(trusted=True), ToolSettings(disabled=["bandit", "gitleaks"]))
    s = run_fix_session(root, reply(fix), [f], hooks(True), verifier=make_verifier(root, suite), backup_base=backup_base)
    rec = s.records[0]
    assert rec.outcome == "applied", rec.detail
    assert {c["name"]: c["status"] for c in rec.checks}["tool:ruff"] == "pass"
    assert (root / "m.py").read_text() == "import sys\n\nprint(sys.argv)\n"


@pytest.mark.skipif(not RUFF.exists(), reason="ruff not installed")
def test_tool_not_reproducing_improvement_rolls_back(tmp_path, backup_base):
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("import os\nimport sys\n\nprint(sys.argv)\n")
    f = Finding(id="F-001", agent="tool:ruff", category="quality/ruff-f401", severity=Severity.LOW,
                title="F401: unused", description="d", file_path="m.py", line=1, evidence="import os",
                confidence=0.95, verification=Verification.VERIFIED)
    cosmetic = {"search": "print(sys.argv)", "replace": "print(sys.argv)  # noqa-free comment"}
    suite = ToolSuite(ApprovalPolicy(trusted=True), ToolSettings(disabled=["bandit", "gitleaks"]))
    s = run_fix_session(root, reply(cosmetic), [f], hooks(True), verifier=make_verifier(root, suite), backup_base=backup_base)
    assert s.records[0].outcome == "rolled-back" and "still reports" in s.records[0].detail


def test_tool_unavailable_makes_verification_inconclusive_not_failed(proj, backup_base):
    f = bare_finding(agent="tool:ruff", category="quality/ruff-e722", title="E722: bare except")
    suite = ToolSuite(ApprovalPolicy(), ToolSettings())  # not trusted, no prompt => denied
    s = run_fix_session(proj, reply(EDIT), [f], hooks(True), verifier=make_verifier(proj, suite), backup_base=backup_base)
    statuses = {c["name"]: c["status"] for c in s.records[0].checks}
    assert statuses["tool:ruff"] == "inconclusive" and s.records[0].outcome == "applied"  # builtin pass is conclusive


PYTEST = Path(sys.executable).parent / "pytest"


@pytest.mark.skipif(not PYTEST.exists(), reason="pytest executable not installed")
def test_run_tests_rolls_back_a_fix_that_breaks_tests(tmp_path, backup_base):
    root = tmp_path / "p"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "conftest.py").write_text("")  # puts the project root on sys.path for pytest
    (root / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    f = Finding(id="F-001", agent="code", category="quality/other", severity=Severity.LOW, title="Improve add",
                description="d", file_path="calc.py", line=2, evidence="return a + b", confidence=0.5,
                verification=Verification.SUPPORTED)
    breaking = {"search": "return a + b", "replace": "return a - b", "reason": "wrong on purpose"}
    s = run_fix_session(root, reply(breaking), [f], hooks(True), verifier=make_verifier(root, run_tests=True),
                        backup_base=backup_base)
    assert s.records[0].outcome == "rolled-back" and "tests" in s.records[0].detail, s.records[0].checks
    assert (root / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


@pytest.mark.skipif(not PYTEST.exists(), reason="pytest executable not installed")
def test_run_tests_needs_approval(tmp_path, backup_base):
    root = tmp_path / "p"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text("def test_x():\n    assert True\n")
    f = Finding(id="F-001", agent="code", category="quality/other", severity=Severity.LOW, title="t", description="d",
                file_path="calc.py", line=2, evidence="return a + b", confidence=0.5, verification=Verification.SUPPORTED)
    fix = {"search": "return a + b", "replace": "return b + a"}
    v = make_verifier(root, run_tests=True, trusted=False)  # no prompt => denied => inconclusive
    s = run_fix_session(root, reply(fix), [f], hooks(True), verifier=v, backup_base=backup_base)
    assert {c["name"]: c["status"] for c in s.records[0].checks}["tests"] == "inconclusive"


# ---------------------------------------------------------------- report
def test_fix_report_contents_and_no_secrets(proj, backup_base):
    (proj / "mod.py").write_text('password = "hunter2secret"\n' + CODE)
    s = run_fix_session(proj, reply(EDIT), [bare_finding(line=5)], hooks(True), verifier=make_verifier(proj),
                        backup_base=backup_base)
    md = render_fix_report(s)
    assert "Applied and verified: **1**" in md and "backed up to" in md and "```diff" in md
    assert "hunter2secret" not in md and json.dumps(s.model_dump())  # serialisable
    none = render_fix_report(run_fix_session(proj, reply(EDIT), [], hooks(True), backup_base=backup_base))
    assert "No files were modified" in none


@pytest.mark.skipif(not PYTEST.exists(), reason="pytest executable not installed")
def test_failing_baseline_tests_are_inconclusive_not_evidence(tmp_path, backup_base):
    root = tmp_path / "p"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text("def test_already_broken():\n    assert False\n")
    f = Finding(id="F-001", agent="code", category="quality/other", severity=Severity.LOW, title="t", description="d",
                file_path="calc.py", line=2, evidence="return a + b", confidence=0.5, verification=Verification.SUPPORTED)
    fix = {"search": "return a + b", "replace": "return b + a"}
    s = run_fix_session(root, reply(fix), [f], hooks(True), verifier=make_verifier(root, run_tests=True),
                        backup_base=backup_base)
    checks = {c["name"]: c for c in s.records[0].checks}
    assert checks["tests"]["status"] == "inconclusive" and "not passing before" in checks["tests"]["detail"]
