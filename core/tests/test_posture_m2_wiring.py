"""posture M2 routes are permitted, admin-gated, audited and configured the way
M1's are — and match the fixed API contract the frontend and MCP code against.
Source-shape by necessity: these defects live between files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from libs.apiscopes import required_scope
from libs.pluginbase import AXES
from libs.rbaccore import BUILTIN_ROLE_PERMS, role_allows

CORE = Path(__file__).resolve().parent.parent
REPO = CORE.parent
SVC = CORE / "services/posture-service"
FINDINGS = (SVC / "app/api/findings.py").read_text()
MAIN = (SVC / "app/main.py").read_text()

CONTRACT = [
    ("GET", "/api/v1/posture/findings"),
    ("GET", "/api/v1/posture/findings/{finding_id}"),
    ("POST", "/api/v1/posture/findings/{finding_id}/status"),
    ("GET", "/api/v1/posture/findings/summary"),
    ("GET", "/api/v1/posture/unmatched"),
    ("POST", "/api/v1/posture/unmatched/{unmatched_id}/assign"),
    ("GET", "/api/v1/posture/scanners"),
    ("POST", "/api/v1/posture/scanners/native/run"),
    ("POST", "/api/v1/posture/scans/import"),
]
ADMIN_ONLY = {"set_finding_status", "assign_unmatched", "run_native", "import_scan"}
READS = {"list_findings", "findings_summary", "get_finding", "list_unmatched", "list_scanners"}


def _routes() -> list[tuple[str, str, str]]:
    out = []
    for m in re.finditer(r'@router\.(get|post)\("([^"]*)"[^)]*\)\nasync def (\w+)\(', FINDINGS):
        out.append((m.group(1).upper(), "/api/v1/posture" + m.group(2), m.group(3)))
    return out


def test_contract_routes_exist():
    assert {(m, p) for m, p, _ in _routes()} == set(CONTRACT)


def test_summary_route_is_declared_before_the_id_route():
    assert FINDINGS.index('@router.get("/findings/summary")') < FINDINGS.index('@router.get("/findings/{finding_id}")')


@pytest.mark.parametrize("method,path,name", _routes())
def test_rbac_reads_for_readers_writes_admin_only(method, path, name):
    header = re.search(rf"async def {name}\((.*?)\):\n", FINDINGS, re.DOTALL).group(1)
    if name in ADMIN_ONLY:
        assert method == "POST" and "Depends(require_admin)" in header, name
    else:
        assert name in READS and method == "GET" and "Depends(get_caller)" in header, name


def test_gateway_matrix_and_pat_scopes_cover_m2_paths():
    assert role_allows(BUILTIN_ROLE_PERMS["support"], "GET", "posture")
    assert not role_allows(BUILTIN_ROLE_PERMS["support"], "POST", "posture")
    assert not role_allows(BUILTIN_ROLE_PERMS["user"], "GET", "posture")
    for m, p in CONTRACT:
        concrete = re.sub(r"\{[^}]+\}", "abc-123", p)[len("/api/v1/"):]
        want = "posture:read" if m == "GET" else "admin:write"
        assert required_scope("posture", m, concrete) == want, (m, p)


@pytest.mark.parametrize("method,path", CONTRACT)
def test_every_m2_route_has_a_named_audit_action(method, path):
    table = re.findall(r'\("(GET|POST|PUT|PATCH|DELETE)", re\.compile\(r"([^"]+)"\), "([^"]+)"', MAIN)
    concrete = re.sub(r"\{[^}]+\}", "abc-123", path)
    hits = [a for m, rx, a in table if m == method and re.match(rx, concrete)]
    assert hits, f"{method} {path} has no audit action"
    if path.endswith("/findings/summary"):
        assert hits[0] == "posture.findings.summary"     # first match wins: not "finding.view"


@pytest.mark.parametrize("action,refusal", [
    ("posture.finding.status", "status change refused"),
    ("posture.unmatched.assign", "assignment refused"),
    ("posture.scan.import", "import refused"),
])
def test_finding_changes_fail_closed_on_audit(action, refusal):
    call = FINDINGS.split(f'action="{action}"', 1)[1].split("except audit.AuditWriteError", 1)
    assert "critical=True" in call[0], action
    assert refusal in call[1].split("\n\n", 1)[0], action


def test_status_change_requires_reason_to_accept_and_never_sets_fixed():
    block = FINDINGS.split("class StatusIn", 1)[1].split("@router", 1)[0]
    assert 'pattern="^(open|accepted)$"' in block
    body = FINDINGS.split("async def set_finding_status", 1)[1].split("\n@router", 1)[0]
    assert 'body.status == "accepted" and not reason' in body


def test_finding_detail_filters_references_on_the_way_out():
    detail = FINDINGS.split("async def _detail", 1)[1].split("\n@router", 1)[0]
    assert 'out["references"] = safe_urls(f.references or [])' in detail


def test_unmatched_queue_is_only_for_callers_who_see_every_host():
    block = FINDINGS.split("async def list_unmatched", 1)[1].split("\n@router", 1)[0]
    assert "if not caller.sees_all_hosts:" in block


def test_summary_keeps_m1_fields_and_adds_m2_counts():
    hosts = (SVC / "app/api/hosts.py").read_text()
    block = hosts.split("async def summary", 1)[1]
    for f in ("hosts_total", "hosts_reporting", "reboot_required", "security_updates", "active_feed_version",
              "feed_age_days", "findings_open", "kev_open"):
        assert f'"{f}"' in block, f
    out = FINDINGS.split("async def findings_summary", 1)[1].split("\n@router", 1)[0]
    for f in ("open_total", "by_severity", "kev_open", "hosts_affected", "feed_stale", "feed_age_days",
              "stale_after_days"):
        assert f'"{f}"' in out, f


def test_scanner_plugins_are_discovered_and_switchable():
    scanning = (SVC / "app/scanning.py").read_text()
    assert 'discover_plugins(f"{__package__}.plugins.scanners", VulnScanner)' in scanning
    assert AXES["scanners"][1] is True                      # an admin may switch a scanner off
    assert '"/api/v1/internal/plugins"' in MAIN
    assert ('"posture_service_url", "posture-service"' in
            (CORE / "services/api-gateway/app/main.py").read_text().split("_PLUGIN_SERVICES", 1)[1].split(")\n", 1)[0])
    assert "disabled_scanners()" in FINDINGS


def test_native_runs_after_every_successful_facts_collection():
    col = (SVC / "app/collector.py").read_text()
    tail = col.split("async def ingest", 1)[1].split("async def poll_once", 1)[0]
    assert "if parsed:" in tail and "scanning.enqueue_native([host_id]" in tail


def test_feed_activation_rescores_and_rematches():
    feeds = (SVC / "app/api/feeds.py").read_text()
    assert "scanning.after_feed_activation()" in feeds.split("async def activate_feed", 1)[1]


def test_facts_collect_debian_source_packages():
    sh = (CORE / "services/automation-service/app/plugins/executors/posture_facts.sh").read_text()
    assert "${source:Package}\\t${source:Version}" in sh


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_stale_days_is_configurable_everywhere(compose):
    env = yaml.safe_load((REPO / compose).read_text())["services"]["posture-service"]["environment"]
    assert env["POSTURE_FEED_STALE_DAYS"] == "${POSTURE_FEED_STALE_DAYS:-7}"
    assert "POSTURE_FEED_STALE_DAYS=7" in (REPO / ".env.example").read_text()


def test_migration_002_follows_001_and_creates_the_tables():
    mig = (SVC / "migrations/versions/002_findings.py").read_text()
    assert 'revision = "posture_002"' in mig and 'down_revision = "posture_001"' in mig
    for t in ("posture_findings", "posture_finding_events", "posture_scans", "posture_unmatched",
              "posture_unmatched_items"):
        assert f'"{t}"' in mig
    assert 'sa.UniqueConstraint("host_id", "cve_id", "package", name="uq_posture_finding_key")' in mig
    for ix in ("ix_posture_findings_status_risk", "ix_posture_findings_host_status", "ix_posture_findings_cve"):
        assert ix in mig


def test_import_is_bounded():
    cfg = (SVC / "app/config.py").read_text()
    assert "posture_scan_max_bytes: int = 64 * 1024 * 1024" in cfg
    assert "settings.posture_scan_max_bytes" in FINDINGS and "settings.posture_scan_max_records" in FINDINGS
