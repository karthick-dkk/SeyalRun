"""Fine-grained PAT scopes: what an agent token can and cannot reach.

The gateway enforces scopes ∩ role for any PAT caller (libs/apiscopes.scope_allows),
so a scoped token does strictly less than its owner. These tests EXECUTE that
decision function against real (segment, method) pairs, and pin the two hard lines
for a PAM: a token can never read a credential secret, and never touch admin.
"""

from __future__ import annotations

from libs.apiscopes import (
    AGENT_GRANTABLE,
    required_scope,
    scope_allows,
    validate_agent_scopes,
)


# ── fine-grained scopes gate exactly one capability each ─────────────────────

def test_read_scope_reads_only_its_domain():
    s = ["inventory:read"]
    assert scope_allows(s, "hosts", "GET", "hosts")            # its domain, read
    assert not scope_allows(s, "hosts", "POST", "hosts")       # write needs inventory:write
    assert not scope_allows(s, "audit", "GET", "audit/logs")   # other domain
    assert not scope_allows(s, "ssh", "POST", "ssh/sessions")  # not a session token


def test_run_is_distinct_from_read_and_write():
    run = ["automation:run"]
    assert scope_allows(run, "job-templates", "POST", "job-templates/abc/run")
    assert not scope_allows(run, "job-templates", "POST", "job-templates")   # create = write
    assert not scope_allows(run, "job-templates", "GET", "job-templates")    # list = read
    # and automation:read/write do NOT grant run
    assert not scope_allows(["automation:read", "automation:write"],
                            "job-templates", "POST", "job-templates/abc/run")


def test_sessions_open_opens_ssh_and_sftp_only():
    s = ["sessions:open"]
    assert scope_allows(s, "ssh", "POST", "ssh/sessions")
    assert scope_allows(s, "sftp", "POST", "sftp/x/upload")
    assert not scope_allows(s, "recordings", "GET", "recordings")  # that's sessions:read


# ── the two hard lines: credentials + admin are never agent-reachable ────────

def test_no_agent_scope_can_read_a_credential():
    everything = list(AGENT_GRANTABLE)
    assert not scope_allows(everything, "credentials", "GET", "credentials")
    assert not scope_allows(everything, "credentials", "GET", "credentials/abc/secret")


def test_no_agent_scope_can_touch_admin():
    everything = list(AGENT_GRANTABLE)
    for seg in ("users", "roles", "authorizations", "api-tokens", "settings"):
        assert not scope_allows(everything, seg, "GET", seg), f"{seg} read leaked"
        assert not scope_allows(everything, seg, "POST", seg), f"{seg} write leaked"


def test_agent_grantable_is_exactly_the_agreed_catalog():
    assert AGENT_GRANTABLE == {
        "inventory:read", "automation:read", "sessions:read",
        "audit:read", "metrics:read", "notifications:read",
        "automation:run", "inventory:write", "sessions:open", "notifications:ack",
    }
    assert not any(s.startswith("credentials:") for s in AGENT_GRANTABLE)
    assert not any(s.startswith("admin:") for s in AGENT_GRANTABLE)


def test_issuance_rejects_non_agent_scopes():
    assert validate_agent_scopes(["inventory:read", "automation:run"]) == []
    assert validate_agent_scopes(["read", "write"]) == []          # legacy still allowed
    bad = validate_agent_scopes(["inventory:read", "credentials:read", "admin:write"])
    assert set(bad) == {"credentials:read", "admin:write"}


# ── backward compatibility: legacy coarse tokens are untouched ───────────────

def test_legacy_coarse_read_write_still_work():
    assert scope_allows(["read"], "hosts", "GET", "hosts")
    assert scope_allows(["read"], "audit", "GET", "audit/logs")
    assert not scope_allows(["read"], "hosts", "POST", "hosts")     # read ≠ write
    assert scope_allows(["write"], "hosts", "POST", "hosts")
    assert scope_allows(["read", "write"], "hosts", "DELETE", "hosts/abc")


# ── deny-by-default: an unclassified segment is closed to PATs ────────────────

def test_unknown_segment_denies_by_default():
    assert required_scope("brand-new-segment", "GET", "brand-new-segment") is None
    assert not scope_allows(list(AGENT_GRANTABLE), "brand-new-segment", "GET", "brand-new-segment")
