"""Unit tests for Ansible output sanitization — importable without ansible-runner."""

import sys
from unittest.mock import MagicMock

# Stub ansible_runner so the module can be imported without the package installed
sys.modules.setdefault("ansible_runner", MagicMock())

from app.runners.ansible_runner import _sanitize_line  # noqa: E402


class TestSanitizeLine:
    def test_normal_line_passes_through(self):
        assert _sanitize_line("TASK [ping] ***") == "TASK [ping] ***"

    def test_ansi_codes_stripped(self):
        assert _sanitize_line("\x1b[32mok\x1b[0m") == "ok"

    def test_line_too_long_truncated(self):
        long_line = "x" * 3000
        result = _sanitize_line(long_line)
        assert len(result) < 3000
        assert result.endswith("[truncated]")

    def test_password_in_output_redacted(self):
        result = _sanitize_line("password: mysecretpassword123")
        assert "mysecretpassword123" not in result
        assert "redacted" in result

    def test_api_key_in_output_redacted(self):
        result = _sanitize_line("api_key=supersecret123456789")
        assert "supersecret123456789" not in result
        assert "redacted" in result

    def test_private_key_header_redacted(self):
        pem_header = "-----BEGIN RSA PRIVATE KEY-----"  # pragma: allowlist secret
        result = _sanitize_line(pem_header)
        assert "PRIVATE KEY" not in result  # pragma: allowlist secret
        assert "redacted" in result

    def test_empty_line_returns_none(self):
        assert _sanitize_line("") is None
        assert _sanitize_line("   ") is None

    def test_normal_output_with_ok_preserved(self):
        line = 'ok: [192.168.64.2] => {"changed": false}'
        result = _sanitize_line(line)
        assert result == line
