# SeyalRun — PCI DSS / SOC 2 control mapping

Scope: the `core/` platform. `modules/jumpserver-legacy/` is quarantined staging
for the Phase 2 port, is not wired into the core stack, and is **out of scope**
for these controls — see [risk-register.md](risk-register.md).

Every claim below cites the code that implements it. Where a control is partial
or absent it is marked as such rather than omitted; an unmarked gap is worse
than a documented one, because an assessor will find it anyway.

Status: **Implemented** / **Partial** / **Gap**.

---

## Access control

| Control | Status | Evidence |
|---|---|---|
| Unique user IDs, no shared accounts | Implemented | `za_users` (`core/schema/*/schema.sql`); seed admin created once by `install.sh` |
| Password hashing | Implemented | Argon2 via `argon2-cffi`, identity-service |
| MFA (TOTP) | Implemented | `pyotp`; enable/disable/verify audited — `identity-service/app/api/auth.py:540,601,642` |
| Password policy + weak-credential detection | Implemented | zxcvbn threshold; `core/tests/test_pwpolicy.py` |
| Account lockout | Implemented | `core/tests/test_lockout_logic.py` |
| Session idle + absolute timeout | Implemented | 30 min idle / 8 h absolute; opaque server-side Redis sessions, not client-decodable JWTs |
| RBAC | Implemented | `core/libs/rbaccore`; resolved per request in api-gateway; `core/tests/test_rbac_merge.py`, `test_gateway_rbac.py` |
| Privilege elevation (break-glass) | Implemented | `core/libs/elevation`; `auth.elevate` audited (`auth.py:785`); `core/tests/test_elevation.py` |
| Login source restriction | Implemented | `za_login_acls`; `login_denied_ip` audited |
| Command restriction in sessions | Implemented | `za_command_filters`; `CommandFilterMatcher` plugin axis |
| Periodic access review | Implemented | `identity-service/app/api/access_review.py` (3 audited actions) |

## Cryptography

| Control | Status | Evidence |
|---|---|---|
| Credentials encrypted at rest | Implemented | AES-256-GCM, `core/libs/dbcore/crypto.py`; `core/tests/test_crypto.py` |
| Envelope encryption (per-row DEK) | Implemented | `inventory-service/app/vault.py`; `core/tests/test_key_hierarchy.py` |
| KEK abstraction for HSM/KMS | Partial | `KeyProvider` axis exists; `env_key_provider.py` is the **only** implementation — KEK is still derived from `ZA_VAULT_PASSWORD`/`ZA_VAULT_SALT`, so it is not hardware-backed |
| Ciphertext is self-describing | **Gap** | No key-version or algorithm prefix in the wire format (`crypto.py`), so rotation must be all-or-nothing and a partial rotation is unrecoverable |
| TLS in transit (external) | Implemented | Terminates at edge-proxy, TLSv1.2/1.3 — `core/services/edge-proxy/templates/default.conf.template:34-41` |
| TLS in transit (internal) | **Gap** | Every internal hop is `http://` (`api-gateway/app/config.py:28-36`). See risk register R-1 |
| Database connection encryption | Implemented | `db_sslmode` defaults to `require` |

## Audit logging (PCI DSS Req 10 / SOC 2 CC7)

| Control | Status | Evidence |
|---|---|---|
| Tamper-evident audit chain | Implemented | SHA-256 `seq`/`prev_hash`/`entry_hash` — `core/libs/audithash`; `core/tests/test_audit_chain.py` proves edit, deletion and reorder are all detected |
| Chain verification endpoint | Implemented | `GET /audit/verify` — `identity-service/app/api/audit.py:30` (admin + service token) |
| Serialized chain writes | Partial | Postgres advisory lock / MySQL `GET_LOCK` — `identity-service/app/audit.py:32`. **Any other engine takes no lock**, so concurrent writes could collide |
| Authentication events logged | Implemented | login, login_failed, login_denied_ip, MFA, password_change, SSO — `identity-service/app/api/auth.py` |
| Authorization/admin changes logged | Implemented | users, roles, groups, authorizations, command filters, login ACLs, tokens, settings |
| **Machine credential access logged** | Implemented | `credential.secret_issued` on `/internal/credentials/{id}/secret`, attributed to the calling service via the verified `iss` claim. **Fail-closed**: the secret is withheld if the audit write fails — `core/tests/test_audit_failclosed.py` |
| Human credential reveal logged | Implemented | `credential.viewed`, incl. elevation flag — `credentials.py:370` |
| Session start logged | Implemented | `session.create` — `terminal-service/app/api/sessions.py:250` |
| Session end logged | Implemented | `session.end` with reason (terminated/closed/error) and duration_seconds — `terminal-service/app/ws/terminal.py`. Duration of privileged access is now in the chain |
| Automation job execution logged | Implemented | `job.start` / `job.finish` with executor, template, target hosts, exit code and attempts — `automation-service/app/runner.py`. Machine triggers are attributed via `details.triggered_by`, never a fake `user_id` |
| Logout logged | **Gap** | No logout endpoint exists |
| Recording playback/download logged | **Gap** | Still no audit on playback/presign. Integrity is now protected (below), access is not. See R-4 |
| Commands typed in a session | Partial | Written to `za_session_commands` (`ws/terminal.py:141`), **not** hash-chained, and write errors are suppressed |
| Recording integrity | Implemented | SHA-256 over canonical frame JSON at ingest, re-checked by `GET /internal/recordings/{id}/verify` — recording-service migration `003_recording_integrity`. Pre-existing recordings have no digest and are reported as unverifiable rather than passing |
| Log redaction | Implemented | `core/libs/securelog/filter.py`; `core/tests/test_securelog_redaction.py` |
| Audit retention | Partial | `AUDIT_LOG_RETENTION_DAYS` configured; no automated archival job, no pre-archival chain verification |
| Audit immutability at the DB layer | Implemented (Postgres/MySQL) | Append-only triggers reject UPDATE/DELETE — identity-service migration `022_audit_immutability`. Other engines are a documented no-op. Superusers can still disable the trigger (residual risk) |

## Secrets management

| Control | Status | Evidence |
|---|---|---|
| No default secrets; fail-fast at startup | Implemented | `core/libs/secrets/require_secrets`, called in every service's `get_settings()`; `core/tests/test_secrets_failfast.py` |
| Service-to-service authentication | Implemented | HS256 `X-Service-Token`, 60 s TTL, audience-bound — `core/libs/servicetoken`; verified inbound by all seven internal services; `core/tests/test_servicetoken.py` |
| Credential rotation | Partial | `za_rotation_policies` with `last_rotated_at`/`next_rotation_due`; `rotate_secret` executor; DB updated only when all hosts succeed |
| Vault key (KEK) rotation | **Gap** | `ops/rotate_vault_key.py` rewrites `secret_ciphertext` only and never touches `wrapped_dek`, so it cannot rotate envelope-encrypted rows — the actual PAM vault. It fails loudly (`InvalidTag`, transaction rolled back) rather than mis-rotating, so this is a capability gap, not a data-integrity one. No dry-run, no audit row. See R-6 |

## Change management / supply chain

| Control | Status | Evidence |
|---|---|---|
| Static analysis, blocking | Implemented | `.github/workflows/security-scan.yml` — semgrep `--error --severity=ERROR` over `core/` and `modules/` |
| Dependency + misconfig scanning | Implemented | Trivy, fails on fixable HIGH/CRITICAL; weekly schedule |
| SBOM | Implemented | CycloneDX via Trivy, uploaded per run |
| Security invariant test suite | Implemented | 151 tests, no DB/Redis required, run on every push |
| Container hardening | Implemented | `read_only: true`, tmpfs, non-root users, healthchecks — `docker-compose.yml` |
| Network exposure | Implemented | Only edge-proxy publishes ports |

---

## Assessor quick-start

```bash
# Audit chain integrity across the full log
curl -sH "Authorization: Bearer $ADMIN_TOKEN" \
     -H "X-Service-Token: $SVC" https://<host>/api/v1/audit/verify
# -> {"ok": true, "checked": <n>, "broken_seq": null, "reason": null}

# Invariant suite (no infrastructure required)
pip install -r core/tests/requirements-test.txt && pytest -q

# Blocking static-analysis gate
semgrep --error --severity=ERROR --config security/scanning/.semgrep.yml core/ modules/
```

`checked` carries two meanings: on success it is the number of rows verified; on
failure it is the sequence number where the chain broke (`core/libs/audithash:44,47`).
Read it together with `ok`.
