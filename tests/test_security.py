import sys

import pytest

from repo_doctor.security.permissions import (
    PathSecurityError,
    is_sensitive_file,
    resolve_repo_root,
    safe_resolve,
)
from repo_doctor.security.redaction import REDACTED, contains_secret, redact


@pytest.mark.parametrize("secret", [
    "sk-ant-abcdefghijklmnop1234567890",
    "ghp_" + "a" * 36,
    "AKIAABCDEFGHIJKLMNOP",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
])
def test_token_shapes_redacted(secret):
    assert secret not in redact(f"value is {secret} here")


def test_assignment_and_url_credentials_redacted():
    out = redact('password = "hunter2secret"\nDB=postgres://admin:hunter2secret@db/app')
    assert "hunter2secret" not in out
    assert "admin:" in out and REDACTED in out


def test_env_references_not_redacted():
    text = 'api_key = os.environ["X"]\ntoken = ${TOKEN}\npassword: $PW'
    assert "os.environ" in redact(text) and "${TOKEN}" in redact(text)


def test_redact_idempotent():
    once = redact("secret = 'abcdef123456'")
    assert redact(once) == once
    assert contains_secret("secret = 'abcdef123456'") and not contains_secret("hello world")


def test_sensitive_files():
    assert is_sensitive_file(".env") and is_sensitive_file(".env.production")
    assert is_sensitive_file("keys/server.pem") and is_sensitive_file("id_rsa")
    assert not is_sensitive_file(".env.example") and not is_sensitive_file("app.py")


def test_path_traversal_blocked(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert safe_resolve(root, "a/b.py") == root.resolve() / "a" / "b.py"
    with pytest.raises(PathSecurityError):
        safe_resolve(root, "../outside.txt")
    with pytest.raises(PathSecurityError):
        safe_resolve(root, "/etc/passwd")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks/permissions")
def test_symlink_escape_blocked(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    (root / "link").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(PathSecurityError):
        safe_resolve(root, "link")


def test_resolve_repo_root_errors(tmp_path):
    with pytest.raises(PathSecurityError):
        resolve_repo_root(tmp_path / "nope")
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(PathSecurityError):
        resolve_repo_root(f)
