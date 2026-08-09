"""Every ${VAR} in the edge-proxy nginx template must be supplied by every
compose file that runs edge-proxy.

nginx's entrypoint (20-envsubst-on-templates.sh) runs envsubst with an explicit
list of the variables that exist in the container environment. A variable the
compose file never sets is therefore not blanked — it is copied into the
rendered config verbatim, and nginx rejects it at parse time:

    [emerg] invalid value "${INTERNAL_PROXY_SSL_VERIFY}" in "proxy_ssl_verify"
            directive, it must be "on" or "off"

edge-proxy is the only host-published service, so that is not a degraded
deployment — the whole stack is unreachable. docker-compose.prod.yml shipped
without INTERNAL_PROTO, INTERNAL_PROXY_SSL_VERIFY or PUBLIC_HTTPS_PORT and
nothing caught it, because docker-compose.yml (the one everybody runs locally)
did define them. Verified against a real nginx container before this test was
written; the test itself is a source-shape check so it needs no Docker in CI.

Regression: ISSUE-005 — found by /qa on 2026-08-09
Report: .gstack/qa-reports/qa-report-192-168-64-12-2026-08-09.md
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "services/edge-proxy/templates/default.conf.template"
REPO = ROOT.parent
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.prod.yml"]

# nginx's own runtime variables use $name, never ${name}, so ${...} in this
# template is unambiguously an envsubst placeholder.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _required_vars() -> set[str]:
    """Placeholders the template needs, ignoring the ones inside comments —
    a comment mentioning ${FOO} imposes no runtime requirement."""
    body = "\n".join(
        line for line in TEMPLATE.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    return set(PLACEHOLDER.findall(body))


def _edge_env(compose_file: str) -> set[str]:
    spec = yaml.safe_load((REPO / compose_file).read_text())
    env = spec["services"]["edge-proxy"].get("environment") or {}
    # Compose accepts both a mapping and a "KEY=value" list.
    return set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}


def test_template_has_placeholders():
    """Guard against the regex silently matching nothing, which would make every
    check below vacuously pass — the same failure mode that hid the audit-chain
    bug (see test_audit_payload_persistence.py)."""
    assert "INTERNAL_PROTO" in _required_vars()


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_compose_supplies_every_template_var(compose_file: str):
    missing = sorted(_required_vars() - _edge_env(compose_file))
    assert not missing, (
        f"{compose_file} does not define {missing} for edge-proxy. envsubst leaves "
        "undefined placeholders verbatim, so nginx fails to start and the only "
        "host-published service never comes up."
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_redirect_preserves_the_published_https_port(compose_file: str):
    """`https://$host$request_uri` always lands on 443 because nginx's $host
    carries no port. On the default 8443 mapping that is a dead port, so every
    plain-HTTP visitor got a connection failure instead of the app."""
    assert "$public_https_port_suffix" in TEMPLATE.read_text(), (
        "the HTTP->HTTPS redirect must carry the published port"
    )
    assert "PUBLIC_HTTPS_PORT" in _edge_env(compose_file)
