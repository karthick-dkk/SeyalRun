"""Every audit forwarder must be hardened the same way.

The forwarder that posts to identity-service's chain is duplicated across
services. terminal-service's copy was left ignoring the HTTP response long after
inventory-service's was fixed, so a privileged session could start and end with
no audit row and no error anywhere — and no behavioural test caught it, because
the service-level tests in this suite mirror the logic rather than importing it.

This is a source-shape check, not a behavioural one. It cannot prove a forwarder
works; it proves the copies have not drifted apart on the properties that made
one of them silently lossy. Delete it once the forwarder lives in one place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SERVICES_DIR = Path(__file__).resolve().parent.parent / "services"


def _forwarders() -> list[Path]:
    """Audit modules that POST to identity-service.

    Discriminated by the presence of httpx rather than an exclusion list, so a
    newly added service is classified correctly without editing this file.
    identity-service owns the chain and writes to the database directly, so its
    audit.py is a writer, not a forwarder, and none of these checks apply to it.
    """
    return sorted(
        path for path in SERVICES_DIR.glob("*/app/audit.py")
        if "httpx" in path.read_text()
    )


def test_at_least_one_forwarder_exists():
    """Guard against the glob silently matching nothing after a refactor, which
    would make every check below vacuously pass."""
    found = _forwarders()
    assert found, f"no */app/audit.py found under {SERVICES_DIR}"


@pytest.mark.parametrize("path", _forwarders(), ids=lambda p: p.parent.parent.name)
def test_forwarder_checks_the_response_status(path: Path):
    """A 4xx/5xx from identity-service must not read as success.

    Without this the entry never receives a sequence number, so verify_chain()
    reports an intact chain while it is quietly incomplete — undetectable by the
    very mechanism meant to detect tampering.
    """
    source = path.read_text()
    assert "raise_for_status()" in source, (
        f"{path.relative_to(SERVICES_DIR.parent)} discards the HTTP response; "
        "a rejected audit entry would count as success"
    )


@pytest.mark.parametrize("path", _forwarders(), ids=lambda p: p.parent.parent.name)
def test_forwarder_supports_fail_closed(path: Path):
    """Each forwarder must offer `critical=` so callers releasing secret material
    can refuse to proceed when the audit write fails."""
    source = path.read_text()
    assert "critical" in source, (
        f"{path.relative_to(SERVICES_DIR.parent)} has no critical flag; "
        "callers cannot fail closed on an audit write failure"
    )


@pytest.mark.parametrize("path", _forwarders(), ids=lambda p: p.parent.parent.name)
def test_forwarder_logs_failures_at_error(path: Path):
    """A dropped audit entry is an error, not a warning — it is a gap in
    compliance evidence, and warning-level tends to be filtered out."""
    source = path.read_text()
    assert "logger.error" in source, (
        f"{path.relative_to(SERVICES_DIR.parent)} does not log forward failures at error level"
    )
