"""posture-service is wired end to end: routed, permitted, audited, deployed.

Each check closes a gap that has shipped before in this repo with a new
segment or service — routed but in no role's perms, a prod compose missing what
dev had, an internal call to a path the callee does not mount, an API route
nobody audits. Source-shape by necessity: the defects live between files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from libs.rbaccore import BUILTIN_ROLE_PERMS, role_allows

CORE = Path(__file__).resolve().parent.parent
REPO = CORE.parent
SVC = CORE / "services/posture-service"


def test_gateway_routes_posture_to_posture_service():
    proxy = (CORE / "services/api-gateway/app/proxy.py").read_text()
    assert '"posture": ("posture_service_url", "posture-service")' in proxy
    cfg = (CORE / "services/api-gateway/app/config.py").read_text()
    assert 'posture_service_url: str = "http://posture-service:8108"' in cfg


def test_role_matrix_reads_for_support_writes_for_admin_none_for_user():
    assert role_allows(BUILTIN_ROLE_PERMS["admin"], "POST", "posture")
    assert role_allows(BUILTIN_ROLE_PERMS["support"], "GET", "posture")
    assert not role_allows(BUILTIN_ROLE_PERMS["support"], "POST", "posture")
    assert not role_allows(BUILTIN_ROLE_PERMS["user"], "GET", "posture")


def test_writes_recheck_the_real_role_downstream():
    """The gateway vouches any authorized write up to X-User-Role: admin; the
    admin-only guard must therefore read X-User-Real-Role."""
    deps = (SVC / "app/deps.py").read_text()
    assert "X-User-Real-Role" in deps
    assert re.search(r"def is_admin.*?return self\.real_role in ADMIN_ROLES", deps, re.DOTALL)
    hosts, feeds = (SVC / "app/api/hosts.py").read_text(), (SVC / "app/api/feeds.py").read_text()
    # Feeds affect every host, so a kiosk login (one host) may not change them either.
    assert "def require_fleet_admin(caller: Caller = Depends(require_admin))" in deps
    for src, name, guard in ((hosts, "collect_host", "require_admin"), (hosts, "collect_all", "require_admin"),
                             (feeds, "import_feed", "require_fleet_admin"), (feeds, "activate_feed", "require_fleet_admin")):
        header = re.search(rf"async def {name}\((.*?)\):\n", src, re.DOTALL)
        assert header and f"Depends({guard})" in header.group(1), name


def _api_routes() -> list[tuple[str, str]]:
    out = []
    for f in (SVC / "app/api").glob("*.py"):
        src = f.read_text()
        prefix = re.search(r'APIRouter\(prefix="([^"]*)"', src)
        for m in re.finditer(r'@router\.(get|post|put|patch|delete)\("([^"]*)"', src):
            out.append((m.group(1).upper(), "/api/v1" + (prefix.group(1) if prefix else "") + m.group(2)))
    return out


def test_every_api_route_has_a_named_audit_action():
    """M1: every API call is audited. The middleware audits all of them; this
    pins that each route also gets a specific action name, not the fallback."""
    main = (SVC / "app/main.py").read_text()
    table = re.findall(r'\("(GET|POST|PUT|PATCH|DELETE)", re\.compile\(r"([^"]+)"\), "([^"]+)"', main)
    assert len(table) >= 8
    routes = _api_routes()
    assert len(routes) == 41, routes  # M1: 8, M2: +9 findings, M3/M4: +24 patching
    for method, path in routes:
        concrete = re.sub(r"\{[^}]+\}", "abc-123", path)
        hits = [a for m, rx, a in table if m == method and re.match(rx, concrete)]
        assert hits, f"{method} {path} has no audit action"


def test_audit_identity_is_only_trusted_behind_a_service_token():
    main = (SVC / "app/main.py").read_text()
    block = main.split("async def audit_every_call", 1)[1]
    assert block.index("verify(") < block.index('request.headers.get("x-user-id")')


def test_trust_state_changes_fail_closed_on_audit():
    feeds = (SVC / "app/api/feeds.py").read_text()
    assert feeds.count("critical=True") >= 2
    for action in ("posture.feed.import", "posture.feed.activate"):
        call = feeds.split(f'action="{action}"', 1)[1].split("except audit.AuditWriteError", 1)[0]
        assert "critical=True" in call, action


def test_posture_calls_the_internal_path_automation_mounts():
    clients = (SVC / "app/clients.py").read_text()
    assert "/api/v1/internal/posture/facts-runs" in clients
    internal = (CORE / "services/automation-service/app/api/internal.py").read_text()
    assert '@router.post("/internal/posture/facts-runs"' in internal
    assert 'include_router(internal_router, prefix="/api/v1")' in \
        (CORE / "services/automation-service/app/main.py").read_text()


def test_facts_executor_runs_only_the_fixed_script():
    """Holding a service token for facts-runs must not be a way to run arbitrary
    commands on every host: the executor overwrites any script-shaped param."""
    src = (CORE / "services/automation-service/app/plugins/executors/posture_facts.py").read_text()
    assert '_CODE_KEYS = ("script_content", "inline_script", "script_args", "run_local")' in src
    assert 'params["script_content"] = FACTS_SCRIPT' in src and 'params["run_local"] = False' in src
    internal = (CORE / "services/automation-service/app/api/internal.py").read_text()
    block = internal.split("class PostureFactsRunCreate", 1)[1].split("@router", 1)[0]
    assert "script" not in block, "the facts-runs request must not accept any script field"
    script = "\n".join(ln for ln in (CORE / "services/automation-service/app/plugins/executors/posture_facts.sh")
                       .read_text().splitlines() if not ln.lstrip().startswith("#"))
    for forbidden in ("apt-get update", "apt-get install", "dnf install", "yum install", "dnf upgrade",
                      "rm -", "sudo "):
        assert forbidden not in script, f"read-only collector contains {forbidden!r}"


def test_production_gate_still_applies_to_facts_runs():
    internal = (CORE / "services/automation-service/app/api/internal.py").read_text()
    block = internal.split("async def create_posture_facts_runs", 1)[1].split("@router", 1)[0]
    assert "_production_gated(" in block and "pending_approval" in block
    # One gate for facts, package, reboot and hook runs. Without a rollout approval it gates.
    gate = internal.split("async def _production_gated", 1)[1].split("@router", 1)[0]
    assert "any_production_hosts" in gate
    assert re.search(r"if approval is None:\n\s+return True", gate)


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_compose_declares_posture_service_the_same_way(compose):
    svc = yaml.safe_load((REPO / compose).read_text())["services"]["posture-service"]
    assert svc["read_only"] is True and svc["env_file"] == ".env"
    assert svc["environment"]["TMPDIR"].startswith("/var/lib/seyalrun/posture")
    assert any(v.startswith("seyalrun_posture_data:/var/lib/seyalrun/posture") for v in svc["volumes"])
    assert "8108/health" in " ".join(svc["healthcheck"]["test"])
    assert set(svc["depends_on"]) == {"identity-service", "inventory-service", "automation-service"}


def test_dev_and_prod_posture_blocks_match_except_image_source():
    dev = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["posture-service"]
    prod = yaml.safe_load((REPO / "docker-compose.prod.yml").read_text())["services"]["posture-service"]
    dev.pop("build")
    prod.pop("image")
    assert dev == prod


def test_internal_tls_overlay_and_cert_list_include_posture():
    tls = yaml.safe_load((REPO / "docker-compose.internal-tls.yml").read_text())["services"]
    assert tls["posture-service"]["environment"]["AUTOMATION_SERVICE_URL"].startswith("https://")
    assert tls["posture-service"]["environment"]["ZABBIX_INTEGRATION_SERVICE_URL"].startswith("https://")
    assert tls["api-gateway"]["environment"]["POSTURE_SERVICE_URL"] == "https://posture-service:8108"
    assert "posture-service" in (REPO / "ops/gen-internal-tls.sh").read_text()


def test_database_is_created_and_migrated_everywhere_a_service_db_is():
    for f in ("core/docker-init/postgres/init-dbs.sh", "core/docker-init/mysql/init-dbs.sh",
              "ops/init-db.sh", "ops/_staging-bootstrap.sh", "docker-compose.db.yml"):
        assert "POSTURE_DB_NAME" in (REPO / f).read_text(), f
    for f in ("install.sh", "ops/_staging-bootstrap.sh", "ops/verify-staging.sh", "ops/deploy-staging-images.sh"):
        assert "posture-service" in (REPO / f).read_text(), f
    assert "POSTURE_DB_NAME=seyalrun_posture" in (REPO / ".env.example").read_text()


def test_image_is_published_and_runs_non_root():
    assert "- posture-service" in (REPO / ".github/workflows/docker-publish.yml").read_text()
    users = [ln.split()[1] for ln in (SVC / "Dockerfile").read_text().splitlines() if ln.startswith("USER ")]
    assert users and users[-1] != "root"
    assert '"--workers", "1"' in (SVC / "Dockerfile").read_text()


def test_host_table_carries_os_family():
    mig = (SVC / "migrations/versions/001_initial.py").read_text()
    assert 'sa.Column("os_family", sa.String(16), nullable=False, server_default="linux")' in mig


def test_no_network_fetch_anywhere_in_posture():
    """Air-gapped: posture talks only to its sibling services, never out."""
    for f in (SVC / "app").rglob("*.py"):
        src = f.read_text()
        assert not re.search(r"https?://(?!(identity|inventory|automation|posture|zabbix-integration)-service)", src.split('"""', 2)[-1]), f
        assert "urllib.request" not in src, f
