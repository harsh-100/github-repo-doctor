import sys

import pytest

from repo_doctor.analyzers.dependencies import parse_requirements_txt
from repo_doctor.analyzers.project import build_profile
from repo_doctor.analyzers.repository import scan_repository
from repo_doctor.core.settings import ScanSettings


def paths(inv):
    return {f.path for f in inv.files}


def test_scan_excludes_unsafe_and_noise(sample_repo):
    inv = scan_repository(sample_repo)
    p = paths(inv)
    assert {"src/app.py", "src/index.js", "package.json"} <= p
    assert ".env" not in p                      # sensitive
    assert "blob.bin" not in p                  # binary
    assert not any(x.startswith("node_modules") for x in p)
    assert inv.skipped["sensitive file"] == 1


def test_scan_respects_gitignore_and_limits(sample_repo):
    (sample_repo / ".gitignore").write_text("src/app.py\n")
    (sample_repo / "big.py").write_text("x = 1\n" * 100_000)
    inv = scan_repository(sample_repo, ScanSettings(max_file_size_kb=10))
    assert "src/app.py" not in paths(inv) and "big.py" not in paths(inv)
    inv = scan_repository(sample_repo, ScanSettings(max_files=2))
    assert len(inv.files) == 2 and inv.truncated


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks/permissions")
def test_scan_does_not_follow_symlinks(sample_repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("x=1")
    (sample_repo / "linkdir").symlink_to(outside, target_is_directory=True)
    (sample_repo / "linkfile.py").symlink_to(outside / "leak.py")
    p = paths(scan_repository(sample_repo))
    assert not any("leak" in x or "linkdir" in x or "linkfile" in x for x in p)


def test_profile_detects_stack(sample_repo):
    profile = build_profile(sample_repo, scan_repository(sample_repo))
    assert set(profile.languages) == {"Python", "JavaScript"}
    assert {"React", "Express", "Flask"} <= set(profile.frameworks)
    assert "Jest" in profile.test_frameworks
    assert "src/index.js" in profile.entry_points
    assert {"npm", "pip"} <= set(profile.package_managers)


def test_profile_handles_malformed_manifest(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    (tmp_path / "a.py").write_text("x=1")
    profile = build_profile(tmp_path, scan_repository(tmp_path))
    assert any("Could not parse manifest" in c for c in profile.concerns)


def test_empty_repo_reports_concern(tmp_path):
    profile = build_profile(tmp_path, scan_repository(tmp_path))
    assert any("No analysable files" in c for c in profile.concerns)


def test_requirements_parsing_keeps_http_prefixed_names(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("httpx==0.27\nhttpie\n-r other.txt\ngit+https://x/y.git\nflask>=3 ; python_version>'3'\n")
    assert [d.name for d in parse_requirements_txt(f)] == ["httpx", "httpie", "flask"]
