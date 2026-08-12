# Staging validation record

Controls exercised against a running stack rather than unit tests alone. Unit
tests prove the logic; these prove the control actually holds once the code is
built into an image, wired through compose, and talking to a real database.

**Environment.** Ubuntu 24.04 aarch64, Docker CE 29.7.2 + Compose v5.4.0, all 10
services built from source on the host, Dockerized Postgres overlay, TLS
terminating at edge-proxy. `ops/verify-staging.sh`: **31/31 checks passed**, every
service's Alembic at head.

Reproduce any of the below with the commands in
[control-mapping.md](control-mapping.md#assessor-quick-start).

---

## Machine credential access is fail-closed (PCI DSS Req 10.2)

A credential was fetched through `/internal/credentials/{id}/secret` — the path
every SSH session and automation job uses — with identity-service healthy, then
stopped, then restored.

| identity-service | HTTP | Secret returned | `credential.secret_issued` rows |
|---|---|---|---|
| up | 200 | yes | 0 → 1 |
| **stopped** | **503** | **no** | 1 (unchanged) |
| restored | 200 | yes | 1 → 2 |

Attribution recorded as `terminal-service`, read from the service token's
verified `iss` claim rather than a caller-supplied header.

> **Correction (2026-08-09).** This section originally reported chain state as
> "2 rows, 2 hashed". That was a **count of hashed rows, not a verification** —
> `verify_chain()` was never run. When it finally was, 84 of 90 rows failed:
> `log_action` hashed `session_id`/`result` into the payload but never persisted
> them, so verification rebuilt a payload missing keys the stored hash covered.
> Fixed; see "Audit chain verification" below. Counting hashed rows is not
> evidence that a chain verifies, and this document should not have implied it
> was.

The middle row is the control: when the audit log cannot be written, the
credential is withheld rather than released unrecorded.

## Audit log is append-only at the database (PCI DSS Req 10.5, R-5)

Issued directly as the application's own database role, not through the API:

```
UPDATE za_audit_logs SET action='tampered' WHERE seq=1;
ERROR:  za_audit_logs is append-only (UPDATE attempted)
HINT:   Audit rows are hash-chained; editing or deleting one breaks verify_chain().

DELETE FROM za_audit_logs WHERE seq=1;
ERROR:  za_audit_logs is append-only (DELETE attempted)
```

Rows intact afterwards. Note this is prevention, not just the chain's detection —
the chain still catches anything that bypasses the trigger (e.g. a superuser
disabling it), which is the documented residual risk.

## Session recordings are tamper-evident (R-4)

A recording was written, verified, then altered directly in the database:

| Step | Result |
|---|---|
| write recording | `201` |
| verify | `ok: true` |
| **frames rewritten in the DB** | — |
| verify | **`ok: false`**, expected `02182bf8…` vs actual `5eecafb9…` |

Recordings predating the digest column report `no digest recorded` rather than
`ok`, so unverifiable content is never reported as verified.

---

## Internal transport encryption (R-1)

Stack brought up with `docker-compose.internal-tls.yml`. All 12 containers
healthy; uvicorn reported `Uvicorn running on https://0.0.0.0:<port>` for every
Python service.

| Probe (run from inside api-gateway) | Result |
|---|---|
| plaintext `http://identity-service:8101/health` | **refused** (`RemoteDisconnected`) |
| `https://…` with the internal CA | `200 {"status":"ok"…}` |
| `https://…` with the CA **removed** from the trust store | **rejected** (`URLError`) |
| end-to-end `GET /api/v1/health` through edge-proxy | `200`, all downstream services `true` |

The third row is the one that matters: it shows verification is actually
enforced, rather than TLS being negotiated with validation silently disabled.

Enabling it is sticky — `ops/_staging-bootstrap.sh` re-adds the overlay whenever
`tls/internal/ca.pem` exists, so a routine redeploy cannot quietly put the mesh
back on plaintext.

## Audit chain verification (2026-08-09)

The first genuine end-to-end run of `verify_chain()` against real data, after the
`session_id`/`result` persistence fix.

| Rows | Verifying |
|---|---|
| 90 written before the fix | **6** — only those written without a `result` |
| 3 written after, each carrying `session_id` and `result` | **3** |

Proof of cause: recomputing seq=1's hash while omitting `result` yields
`e546b9be…`; including `result="success"` yields `bf8e1b24…`, which is the stored
value — while the row's `result` column reads `None`.

**The 84 pre-fix rows are permanently unverifiable.** The hashed values were never
stored, so they cannot be recovered, and migration 022's immutability trigger
blocks UPDATE by design. Staging needs a chain re-baseline (fresh identity
database) before its audit evidence means anything to an assessor.

## What this record does not cover

Stated so the gaps are not read as passes:

- **The edge-proxy→frontend hop** — still HTTP. That container is nginx, not
  uvicorn, and serves static assets only, so it has no issued certificate.
- **Client authentication at the transport layer** — this is one-way TLS.
  Callers are authenticated by the signed `X-Service-Token`, not client certs.
- **Recording playback/presign auditing** — the integrity half of R-4 is closed,
  the access-logging half is not.
- **`ops/verify-staging.sh` login/PAT/CRUD checks** — skipped, because
  `SEYALRUN_ADMIN_PASSWORD` was not set for this run (the deploy reused an
  existing `.env`). Run `ops/reset-admin-password.sh` and re-run to cover them.
- **The JumpServer module** — not deployed here; `modules/jumpserver-legacy/` is
  not wired into this stack.
