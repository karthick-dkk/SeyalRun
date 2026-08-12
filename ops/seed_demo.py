#!/usr/bin/env python3
"""Populate a SeyalRun deployment with obviously-fake demo data.

Run inside the inventory-service container (it has libs/, database access and the
vault key). See ops/seed-demo.sh, which is the supported entry point.

Why this exists: a stranger who clones the repo and starts the stack sees a login
page and an empty system. Nothing explains what the product does or why the audit
chain matters, so most people leave. Adoption is the whole distribution strategy
for an open-source product, and the first sixty seconds is where it is won.

Everything written here is unmistakably fake — hosts live in the reserved
TEST-NET-3 range (203.0.113.0/24, RFC 5737, never routable), names are prefixed,
and the credential password says so out loud. That matters because this script
writes real rows into a real database, and someone will eventually run it
somewhere they did not intend.

Audit rows are written through identity-service's HTTP endpoint rather than
straight into the table, so every demo entry is properly sequenced and hash-chained
and `/audit/verify` returns a genuine, verifiable result. Fabricating chain rows
directly would produce a demo that lies about the one property being demonstrated.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from app.config import get_settings
from app.database import SessionLocal
from app.models import ZACredential, ZAHost
from app.vault import encrypt_envelope
from libs.servicetoken import mint
from sqlalchemy import delete, select

DEMO_PREFIX = "demo-"
DEMO_MARKER = "SEYALRUN DEMO DATA - NOT REAL"

HOSTS = [
    ("demo-web-01", "203.0.113.11", "linux"),
    ("demo-web-02", "203.0.113.12", "linux"),
    ("demo-db-primary", "203.0.113.21", "linux"),
    ("demo-db-replica", "203.0.113.22", "linux"),
    ("demo-payments-api", "203.0.113.31", "linux"),
    ("demo-bastion", "203.0.113.41", "linux"),
]

# A plausible day in the life of an estate, so the timeline reads like something
# real rather than six identical rows.
STORY = [
    ("login", "alice", "authentication", "success"),
    ("session.create", "alice", "ssh_session", "success"),
    ("credential.secret_issued", "alice", "credential", "success"),
    ("session.end", "alice", "ssh_session", "closed"),
    ("login_failed", "mallory", "authentication", "failure"),
    ("login_denied_ip", "mallory", "authentication", "failure"),
    ("credential.viewed", "bob", "credential", "success"),
    ("job.start", "bob", "job_run", "started"),
    ("job.finish", "bob", "job_run", "success"),
    ("auth.elevate", "alice", "authentication", "success"),
]


def _ssl_ctx() -> ssl.SSLContext | None:
    """Honour SSL_CERT_FILE so this works with internal TLS enabled or disabled."""
    bundle = os.environ.get("SSL_CERT_FILE")
    return ssl.create_default_context(cafile=bundle) if bundle else None


def _post_audit(settings, entry: dict) -> int:
    token = mint("inventory-service", "identity-service", settings.service_jwt_secret)
    req = urllib.request.Request(
        f"{settings.identity_service_url}/api/v1/internal/audit",
        data=json.dumps(entry).encode(),
        headers={"X-Service-Token": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as resp:
        return resp.status


async def _guard(session) -> None:
    """Refuse to seed a system that already holds real data.

    A demo seeder is exactly the kind of tool that gets pointed at the wrong
    database once. Bail unless every existing host and credential is demo-prefixed,
    which is cheap insurance against writing fake payment servers into production.
    """
    if os.environ.get("SEYALRUN_DEMO_FORCE") == "1":
        print("[!] SEYALRUN_DEMO_FORCE=1 — skipping the real-data guard.")
        return

    real_hosts = [h for h in (await session.execute(select(ZAHost))).scalars() if not h.name.startswith(DEMO_PREFIX)]
    real_creds = [c for c in (await session.execute(select(ZACredential))).scalars() if not c.name.startswith(DEMO_PREFIX)]
    if real_hosts or real_creds:
        print(
            f"[X] Refusing to seed: found {len(real_hosts)} non-demo host(s) and "
            f"{len(real_creds)} non-demo credential(s).\n"
            "    This looks like a real deployment. Set SEYALRUN_DEMO_FORCE=1 only "
            "if you are certain.",
            file=sys.stderr,
        )
        raise SystemExit(2)


async def main() -> None:
    settings = get_settings()

    async with SessionLocal() as session:
        await _guard(session)

        # Idempotent: wipe prior demo rows so re-running is safe and repeatable.
        await session.execute(delete(ZACredential).where(ZACredential.name.like(f"{DEMO_PREFIX}%")))
        await session.execute(delete(ZAHost).where(ZAHost.name.like(f"{DEMO_PREFIX}%")))
        await session.commit()

        ct, wrapped = encrypt_envelope(json.dumps({"password": f"{DEMO_MARKER} - not a real password"}))
        cred = ZACredential(
            id=str(uuid.uuid4()),
            name="demo-root-credential",
            username="root",
            secret_type="password",
            secret_ciphertext=ct,
            wrapped_dek=wrapped,
        )
        session.add(cred)

        for name, ip, os_type in HOSTS:
            session.add(ZAHost(id=str(uuid.uuid4()), name=name, ip=ip, os_type=os_type, port=22))
        await session.commit()
        print(f"[*] Seeded {len(HOSTS)} hosts and 1 credential (all '{DEMO_PREFIX}' prefixed).")

    # Chain-writes go through identity-service so /audit/verify is genuinely valid.
    now = datetime.now(timezone.utc)
    written = 0
    for i, (action, user, resource, result) in enumerate(STORY):
        status = _post_audit(
            settings,
            {
                "user_id": None,
                "username": user,
                "action": action,
                "resource_type": resource,
                "resource_id": f"demo-{i}",
                "details": {"demo": True, "note": DEMO_MARKER,
                            "occurred_at": (now - timedelta(minutes=len(STORY) - i)).isoformat()},
                "ip_address": "203.0.113.200",
                "session_id": None,
                "result": result,
            },
        )
        if status < 400:
            written += 1

    print(f"[*] Wrote {written}/{len(STORY)} hash-chained audit entries.")
    print("[OK] Demo data ready. Verify the chain: GET /api/v1/audit/verify")


if __name__ == "__main__":
    asyncio.run(main())
