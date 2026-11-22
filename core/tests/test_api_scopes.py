"""Fine-grained PAT scopes: what an agent token can and cannot reach.

The gateway enforces scopes ∩ role for any PAT caller (libs/apiscopes.scope_allows),
so a scoped token does strictly less than its owner. These tests EXECUTE that
decision function against real (segment, method) pairs, and pin the two hard lines
for a PAM: a token can never read a credential secret, and never touch admin.
"""

from __future__ import annotations

from pathlib import Path

from libs.apiscopes import (
    AGENT_GRANTABLE,
    has_legacy_scopes,
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
        "posture:read", "patch:approve", "policy:write",
    }
    assert not any(s.startswith("credentials:") for s in AGENT_GRANTABLE)
    assert not any(s.startswith("admin:") for s in AGENT_GRANTABLE)


def test_issuance_rejects_non_agent_scopes():
    assert validate_agent_scopes(["inventory:read", "automation:run"]) == []
    assert set(validate_agent_scopes(["read", "write"])) == {"read", "write"}  # F-1: no longer issued
    bad = validate_agent_scopes(["inventory:read", "credentials:read", "admin:write"])
    assert set(bad) == {"credentials:read", "admin:write"}


def test_legacy_tokens_are_flagged_for_reissue():
    assert has_legacy_scopes(["read"])
    assert has_legacy_scopes(["inventory:read", "write"])
    assert not has_legacy_scopes(["inventory:read"])
    assert not has_legacy_scopes(None)


def test_new_tokens_have_no_default_scope():
    """F-1: the issuance schema and the ORM must not hand a token a scope it was
    not explicitly given — the old default was the coarse "read"."""
    root = Path(__file__).resolve().parent.parent / "services/identity-service/app"
    schemas = (root / "schemas.py").read_text()
    block = schemas.split("class ApiTokenCreate", 1)[1].split("class ", 1)[0]
    assert '["read"]' not in block and "default_factory=list" in block
    assert 'default=lambda: ["read"]' not in (root / "models.py").read_text()
    tokens = (root / "api/tokens.py").read_text()
    assert "if not payload.scopes" in tokens, "empty scope list must be refused at issuance"


# ── backward compatibility: existing legacy coarse tokens keep working ───────

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


# ── posture (3.0): read is delegable; collect/import/activate are not ─────────

def test_posture_read_scope_reads_posture_only():
    s = ["posture:read"]
    for path in ("posture/hosts", "posture/hosts/abc", "posture/feeds", "posture/summary"):
        assert scope_allows(s, "posture", "GET", path), path
    assert not scope_allows(s, "hosts", "GET", "hosts")        # not inventory
    assert not scope_allows(s, "posture", "POST", "posture/collect")


def test_no_agent_scope_can_collect_import_or_activate():
    """Admin-only actions sit on the admin:write sentinel, which no token holds."""
    everything = list(AGENT_GRANTABLE)
    for path in ("posture/collect", "posture/hosts/abc/collect", "posture/feeds/import",
                 "posture/feeds/abc/activate"):
        assert required_scope("posture", "POST", path) == "admin:write", path
        assert not scope_allows(everything, "posture", "POST", path), path


def test_patch_approve_and_policy_write_are_narrow():
    assert required_scope("posture", "POST", "posture/patches/p1/approve") == "patch:approve"
    assert required_scope("posture", "PUT", "posture/policies/x") == "policy:write"
    assert required_scope("posture", "POST", "posture/policies") == "policy:write"
    # neither leaks into the other, nor into other posture writes
    assert not scope_allows(["patch:approve"], "posture", "PUT", "posture/policies/x")
    assert not scope_allows(["policy:write"], "posture", "POST", "posture/patches/p1/approve")
    assert not scope_allows(["patch:approve", "policy:write"], "posture", "POST", "posture/feeds/import")
    assert not scope_allows(["patch:approve"], "posture", "GET", "posture/hosts")


def test_posture_scope_ignores_dot_segments():
    from libs.apiscopes import required_scope, scope_allows
    assert required_scope("posture", "POST", "posture/policies/abc") == "policy:write"
    assert required_scope("posture", "POST", "posture/patches/p1/approve") == "patch:approve"
    for p in ("posture/policies/../feeds/import", "posture/patches/x/../../feeds/import/approve"):
        assert not scope_allows(["policy:write", "patch:approve"], "posture", "POST", p)
