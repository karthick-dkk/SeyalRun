"""WebSocket upstreams must follow the same transport as their HTTP peers, and
must authenticate the caller.

Two defects, found together, both of which made every WebSocket feature in the
product fail or fail open under `docker-compose.internal-tls.yml`.

1. **Scheme drift.** The gateway held the WS upstream URL as a *second*,
   independent setting next to the HTTP one. The internal-TLS overlay rewrites
   only the http:// values, so the gateway went on dialling ws:// at a listener
   that had become TLS-only. SSH terminal, live job logs and notifications all
   died at once with "did not receive a valid HTTP response" — and because the
   proxy catches that and closes 1000, the browser saw a *clean* close and
   reconnected forever. The overlay's own header claims "No application code
   changes are needed"; that was true for httpx (SSL_CERT_FILE) and false here.

2. **Unauthenticated upgrades.** automation-service mounts its two WS routes on
   the app rather than the service-token-guarded router, so their paths match
   what the proxy dials. That also took them off the router's dependency, and
   nothing then checked the caller: anything able to reach port 8105 could set
   X-User-Id and read another user's notifications, or tail any run's job log.
   terminal-service verifies the token by hand in exactly this situation; these
   two did not.

Source-shape checks. The first defect lives in the gap between a compose overlay
and a config default, the second between a route decorator and a router it is no
longer attached to — neither is visible to a unit test of either side alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
GATEWAY_CONFIG = SERVICES / "api-gateway/app/config.py"
OVERLAY = ROOT.parent / "docker-compose.internal-tls.yml"


# ── 1. scheme drift ──────────────────────────────────────────────────────────

def test_ws_urls_are_derived_not_independently_defaulted():
    """A hardcoded ws:// default is the bug: the overlay cannot rewrite what it
    does not know to look for."""
    src = GATEWAY_CONFIG.read_text()
    hardcoded = re.findall(r'(\w+_ws_url):\s*str\s*=\s*"(ws{1,2}://[^"]+)"', src)
    assert not hardcoded, (
        "WS upstream URLs must derive their scheme from the matching HTTP URL, not "
        f"carry an independent default that internal TLS silently invalidates: {hardcoded}"
    )
    assert "_derive_ws_urls" in src, "expected a validator deriving the WS URLs"


def test_every_ws_route_target_has_a_derived_url():
    """Guard against a future third WS upstream repeating this."""
    proxy = (SERVICES / "api-gateway/app/ws_proxy.py").read_text()
    attrs = set(re.findall(r'\(\s*"(\w+_ws_url)"\s*,', proxy))
    assert attrs, "could not parse WS_SERVICE_ROUTES — this test would pass vacuously"
    src = GATEWAY_CONFIG.read_text()
    derived = set(re.findall(r'for svc in \(([^)]*)\)', src))
    assert derived, "no derivation loop found"
    names = set(re.findall(r'"(\w+)"', next(iter(derived))))
    for attr in attrs:
        assert attr.removesuffix("_ws_url") in names, (
            f"{attr} is dialled by ws_proxy but never derived — it will keep the "
            "wrong scheme when internal TLS is enabled"
        )


def test_scheme_mapping_is_correct():
    """The mapping itself, executed rather than eyeballed."""
    ns: dict = {}
    src = GATEWAY_CONFIG.read_text()
    body = src[src.index("def _ws_scheme"):src.index("class Settings")]
    exec(compile(body, "<config>", "exec"), ns)
    ws = ns["_ws_scheme"]
    assert ws("http://terminal-service:8103") == "ws://terminal-service:8103"
    assert ws("https://terminal-service:8103") == "wss://terminal-service:8103"
    # https must not be matched by the http prefix first
    assert not ws("https://x:1").startswith("ws://")


def test_internal_tls_overlay_switches_every_service_to_https():
    """If a service is left on http:// here, its peers reach it over plaintext
    while it serves TLS — the same class of failure, one service at a time."""
    overlay = yaml.safe_load(OVERLAY.read_text())
    bad = []
    for name, svc in (overlay.get("services") or {}).items():
        for key, val in (svc.get("environment") or {}).items():
            if key.endswith("_SERVICE_URL") and str(val).startswith("http://"):
                bad.append(f"{name}.{key}={val}")
    assert not bad, f"internal-TLS overlay still points at plaintext peers: {bad}"


# ── 2. unauthenticated WS upgrades ───────────────────────────────────────────

# service -> WS routes declared directly on the app object (i.e. off the router
# and therefore off its service-token dependency).
@pytest.mark.parametrize("service", ["automation-service", "terminal-service"])
def test_app_level_ws_routes_verify_the_service_token(service: str):
    main = (SERVICES / service / "app/main.py").read_text()
    routes = re.findall(r'@app\.websocket\("([^"]+)"\)\s*\nasync def (\w+)\(([^)]*)\)', main)
    if not routes:
        pytest.skip(f"{service} declares no app-level WS routes")
    for path, func, _sig in routes:
        body = main[main.index(f"async def {func}("):]
        body = body[: body.find("\n@")] if "\n@" in body else body
        handler_names = re.findall(r'await (\w+)\(', body)
        guarded = "require_ws_service_token" in body or any(
            _handler_verifies(service, h) for h in handler_names
        )
        assert guarded, (
            f"{service} {path} accepts a WebSocket upgrade without verifying "
            "X-Service-Token — anything on the internal network can set X-User-Id "
            "and impersonate a user on it"
        )


def _handler_verifies(service: str, handler: str) -> bool:
    """True if the delegate this route awaits verifies the token itself
    (terminal-service's handle_terminal/handle_spectate do)."""
    for f in (SERVICES / service / "app").rglob("*.py"):
        src = f.read_text()
        if f"def {handler}(" not in src:
            continue
        body = src[src.index(f"def {handler}("):]
        if 'verify(' in body[:2000] and "service_jwt_secret" in body[:2000]:
            return True
    return False


def test_the_guard_closes_and_does_not_merely_log():
    """A guard that returns without closing leaves the socket open and unauthenticated."""
    deps = (SERVICES / "automation-service/app/deps.py").read_text()
    body = deps[deps.index("async def require_ws_service_token"):]
    body = body[: body.index("\ndef ")] if "\ndef " in body else body
    assert "websocket.close(" in body, "the guard must close the socket on failure"
    assert "return False" in body and "return True" in body
