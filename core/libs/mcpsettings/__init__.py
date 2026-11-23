"""MCP (AI-agent) settings — the ONE definition of their shape and defaults.

Stored as the ``mcp`` row of identity-service's za_settings (the same
platform-settings table as ``platform`` / ``zabbix_module``), edited at
``PUT /api/v1/settings/mcp`` and read by:

  * api-gateway — per-token MCP rate limit, the run-automation allowlist (every
    PAT), and the defence-in-depth enabled/read-only re-check;
  * mcp-server — refuses initialize/tool calls when disabled and hides/refuses
    the write tools in read-only mode (via the gateway's ``GET /api/v1/mcp/policy``).

DEFAULTS equal the behaviour before these settings existed, so an upgrade with no
row in the table changes nothing: MCP on, write tools on, every enabled playbook
runnable (``agent_template_ids: None``). The one new limit, 60 calls per token
per minute, is the spec'd default.

Stdlib-only and pure so the test suite loads it directly, and so mcp-server can
ship it without pulling in any other lib.
"""

from __future__ import annotations

import re

CALLS_PER_MINUTE_CHOICES: tuple[int, ...] = (30, 60, 120)

DEFAULTS: dict = {
    "enabled": True,
    "allow_write_tools": True,
    "calls_per_minute": 60,
    # None = no agent allowlist: any enabled template the token's owner may run
    # (today's behaviour). A list (even empty) = only these template ids.
    "agent_template_ids": None,
}

_MAX_TEMPLATE_IDS = 500
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# POST job-templates/{id}/run — the only route that starts a playbook.
_RUN_PATH = re.compile(r"job-templates/([A-Za-z0-9_-]{1,64})/run/?")


def defaults() -> dict:
    out = dict(DEFAULTS)
    out["agent_template_ids"] = None
    return out


def normalize(raw: object) -> dict:
    """Coerce a stored/fetched settings document into a complete, valid one.

    Anything missing or malformed falls back to the DEFAULT for that key (never
    to a more restrictive or more permissive invented value), so a partial row
    written by an older version, or garbage, still yields today's behaviour."""
    out = defaults()
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("enabled"), bool):
        out["enabled"] = raw["enabled"]
    if isinstance(raw.get("allow_write_tools"), bool):
        out["allow_write_tools"] = raw["allow_write_tools"]
    cpm = raw.get("calls_per_minute")
    if isinstance(cpm, int) and not isinstance(cpm, bool) and cpm in CALLS_PER_MINUTE_CHOICES:
        out["calls_per_minute"] = cpm
    ids = raw.get("agent_template_ids")
    if isinstance(ids, list):
        out["agent_template_ids"] = clean_template_ids(ids)
    return out


def clean_template_ids(ids: list) -> list[str]:
    """Dedupe, keep order, drop anything that is not a plausible id."""
    seen: dict[str, None] = {}
    for i in ids:
        if isinstance(i, str) and _ID_RE.fullmatch(i.strip()):
            seen.setdefault(i.strip(), None)
        if len(seen) >= _MAX_TEMPLATE_IDS:
            break
    return list(seen)


def public_policy(settings: dict) -> dict:
    """What mcp-server (and any token holder) may learn: the switches it enforces.
    The template allowlist is enforced by the gateway and not disclosed here."""
    s = normalize(settings)
    return {"enabled": s["enabled"], "allow_write_tools": s["allow_write_tools"],
            "calls_per_minute": s["calls_per_minute"]}


def run_template_id(method: str, path: str) -> str | None:
    """Template id when (method, path) starts a playbook run, else None."""
    if method.upper() != "POST":
        return None
    m = _RUN_PATH.fullmatch(path.strip("/"))
    return m.group(1) if m else None


def template_allowed(settings: dict, template_id: str) -> bool:
    ids = normalize(settings)["agent_template_ids"]
    return ids is None or template_id in ids
