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
verified `iss` claim rather than a caller-supplied header. Chain state after:
**2 rows, 2 hashed**.

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

## What this record does not cover

Stated so the gaps are not read as passes:

- **R-1, internal transport encryption** — open and accepted. Inter-service
  traffic is plaintext inside the Docker network; see the risk register for the
  measured remediation scope.
- **Recording playback/presign auditing** — the integrity half of R-4 is closed,
  the access-logging half is not.
- **`ops/verify-staging.sh` login/PAT/CRUD checks** — skipped, because
  `SEYALRUN_ADMIN_PASSWORD` was not set for this run (the deploy reused an
  existing `.env`). Run `ops/reset-admin-password.sh` and re-run to cover them.
- **The JumpServer module** — not deployed here; `modules/jumpserver-legacy/` is
  not wired into this stack.
