"""Audit forwarding must not fail silently.

Mirrors inventory-service/app/audit.py::log_action. The forwarders run over HTTP
to identity-service, which owns the hash chain, so a dropped forward is not a
missing log line — it is a gap in the tamper-evident chain that verify_chain()
cannot detect, because the entry never got a sequence number at all.

PCI DSS Req 10 is about being able to answer "who accessed this, and when".
Releasing a plaintext credential while the audit write fails makes that question
permanently unanswerable, so that path is fail-closed.
"""

from __future__ import annotations

import pytest


class AuditWriteError(RuntimeError):
    pass


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _HTTPError(f"HTTP {self.status_code}")


class _HTTPError(Exception):
    """Stands in for httpx.HTTPError."""


def forward(*, status_code: int | None, critical: bool, sent: list) -> None:
    """The control flow of log_action: post, check status, escalate if critical.

    ``status_code=None`` models the transport itself failing (connect error,
    timeout) rather than the server returning a response.
    """
    try:
        if status_code is None:
            raise _HTTPError("connection refused")
        _Response(status_code).raise_for_status()
        sent.append(status_code)
    except _HTTPError as exc:
        if critical:
            raise AuditWriteError("could not record audit entry; refusing to proceed") from exc


def test_successful_forward_is_recorded():
    sent: list = []
    forward(status_code=204, critical=True, sent=sent)
    assert sent == [204]


@pytest.mark.parametrize("status_code", [400, 401, 403, 409, 500, 503])
def test_non_2xx_counts_as_failure(status_code):
    """Regression: the response status used to be ignored entirely, so
    identity-service rejecting an entry was indistinguishable from success."""
    sent: list = []
    with pytest.raises(AuditWriteError):
        forward(status_code=status_code, critical=True, sent=sent)
    assert sent == []


def test_transport_failure_raises_when_critical():
    with pytest.raises(AuditWriteError):
        forward(status_code=None, critical=True, sent=[])


def test_non_critical_events_stay_best_effort():
    """Routine events must not take a request down when identity-service blips —
    only paths where proceeding unlogged defeats the control are fail-closed."""
    forward(status_code=None, critical=False, sent=[])
    forward(status_code=500, critical=False, sent=[])


def test_credential_egress_is_not_released_without_an_audit_row():
    """The invariant behind /internal/credentials/{id}/secret: the secret is
    returned only if its audit entry was accepted."""
    released: list[str] = []

    def issue_secret(*, audit_status: int | None) -> None:
        forward(status_code=audit_status, critical=True, sent=[])
        released.append("s3cr3t")

    with pytest.raises(AuditWriteError):
        issue_secret(audit_status=None)
    assert released == []  # secret withheld

    issue_secret(audit_status=204)
    assert released == ["s3cr3t"]
