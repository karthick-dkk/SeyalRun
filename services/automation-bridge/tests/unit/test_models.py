"""Unit tests for Pydantic models — validate security constraints."""

import pytest
from pydantic import ValidationError

from app.models import AnsibleRunRequest


def make_valid_request(**overrides) -> dict:
    base = {
        "playbook": "ping.yml",
        "inventory_selector": "jumpserver",
        "extra_vars": {},
        "timeout_seconds": 60,
        "triggered_by": "karthick",
    }
    base.update(overrides)
    return base


class TestAnsibleRunRequest:
    def test_valid_request_accepted(self):
        req = AnsibleRunRequest(**make_valid_request())
        assert req.playbook == "ping.yml"

    def test_playbook_with_path_traversal_rejected(self):
        with pytest.raises(ValidationError, match="pattern"):
            AnsibleRunRequest(**make_valid_request(playbook="../etc/passwd"))

    def test_playbook_with_shell_chars_rejected(self):
        with pytest.raises(ValidationError, match="pattern"):
            AnsibleRunRequest(**make_valid_request(playbook="ping.yml; rm -rf /"))

    def test_playbook_without_yml_extension_rejected(self):
        with pytest.raises(ValidationError, match="pattern"):
            AnsibleRunRequest(**make_valid_request(playbook="ping"))

    def test_playbook_too_long_rejected(self):
        with pytest.raises(ValidationError):
            AnsibleRunRequest(**make_valid_request(playbook="a" * 101 + ".yml"))

    def test_extra_fields_rejected(self):
        """extra=forbid must block unknown fields (parameter pollution protection)."""
        with pytest.raises(ValidationError, match="Extra inputs"):
            AnsibleRunRequest(**make_valid_request(injected_field="evil"))

    def test_timeout_minimum_enforced(self):
        with pytest.raises(ValidationError, match="greater than or equal to 30"):
            AnsibleRunRequest(**make_valid_request(timeout_seconds=10))

    def test_timeout_maximum_enforced(self):
        with pytest.raises(ValidationError, match="less than or equal to 3600"):
            AnsibleRunRequest(**make_valid_request(timeout_seconds=7200))

    def test_inventory_selector_too_long_rejected(self):
        with pytest.raises(ValidationError):
            AnsibleRunRequest(**make_valid_request(inventory_selector="h" * 501))

    def test_strict_mode_no_string_to_int_coercion(self):
        """strict=True: string "60" must not be accepted as int 60."""
        with pytest.raises(ValidationError):
            AnsibleRunRequest(**make_valid_request(timeout_seconds="60"))
