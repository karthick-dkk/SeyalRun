"""Every SeyalRun container drops all Linux capabilities and blocks privilege gain (F-9).

The service images run as an unprivileged user (appuser / nginx), so they need
no capabilities at all. Redis is the one exception: its entrypoint starts as
root, chowns /data and switches to the redis user, which needs CHOWN, SETUID
and SETGID and nothing else. Database containers in docker-compose.db.yml are
upstream images with their own init requirements and are out of scope here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.prod.yml"]
REDIS_CAPS = {"CHOWN", "SETUID", "SETGID"}


def _services(compose: str) -> dict:
    return yaml.safe_load((REPO / compose).read_text())["services"]


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_every_service_drops_all_capabilities(compose):
    services = _services(compose)
    assert len(services) >= 12, f"expected the full stack in {compose}, got {sorted(services)}"
    for name, svc in services.items():
        assert svc.get("cap_drop") == ["ALL"], f"{compose}:{name} must cap_drop: [ALL]"
        assert "no-new-privileges:true" in (svc.get("security_opt") or []), f"{compose}:{name} needs no-new-privileges"


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_only_redis_adds_capabilities_back_and_only_what_it_needs(compose):
    for name, svc in _services(compose).items():
        added = set(svc.get("cap_add") or [])
        if name == "redis":
            assert added <= REDIS_CAPS, f"redis adds {added - REDIS_CAPS} beyond its privilege drop"
        else:
            assert not added, f"{compose}:{name} adds capabilities {added}"


@pytest.mark.parametrize("service", ["api-gateway", "identity-service", "inventory-service", "terminal-service",
                                     "recording-service", "automation-service", "zabbix-integration-service",
                                     "metrics-service", "posture-service", "mcp-server", "edge-proxy",
                                     "frontend"])
def test_service_images_run_as_non_root(service):
    """The premise of dropping every capability: the process is not root."""
    dockerfile = (REPO / "core/services" / service / "Dockerfile").read_text()
    users = [line.split()[1] for line in dockerfile.splitlines() if line.startswith("USER ")]
    assert users and users[-1] != "root", f"{service} must end on a non-root USER, got {users}"
