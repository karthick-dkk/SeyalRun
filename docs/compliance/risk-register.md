# SeyalRun — security risk register

Open gaps against PCI DSS / SOC 2, with the code that causes them. Companion to
[control-mapping.md](control-mapping.md).

This register exists to be shown to an assessor. An accepted, documented risk is
defensible; the same risk found undocumented is a finding about the process, not
just the code.

Severity reflects exploitability in a deployed system, not how hard it is to fix.

---

## R-1 — No encryption between internal services

**Severity: High.** PCI DSS Req 4.

TLS terminates at edge-proxy; every hop behind it is plaintext HTTP
(`core/services/api-gateway/app/config.py:28-36`, and every `proxy_pass` in
`core/services/edge-proxy/templates/default.conf.template`). Service tokens,
decrypted credentials returned by `/internal/credentials/{id}/secret`, and live
session data all cross the Docker bridge network in the clear. Anyone with
packet capture on the host, or a container on the same network, can read them.

Mitigating: only edge-proxy publishes a port, and every internal endpoint
requires a signed `X-Service-Token`. So this is a confidentiality gap against a
host-level or container-level attacker, not a remote one.

**Remediation.** Issue per-service certificates from a small internal CA created
at install time in `docker-init/`, and have each service terminate TLS itself.
A full service mesh is disproportionate for a Compose-first product. Until then
this must be declared in scope documentation as an accepted risk with the
compensating controls above.

## R-2 — Session end is not audited — CLOSED

**Severity: Medium.** PCI DSS Req 10.2. **Closed**: `session.end` is emitted from the disconnect/idle path with a reason and `duration_seconds`, so the length of privileged access is now chained.

Original finding:

`terminal-service/app/ws/terminal.py:309,372` sets `ended_at` on normal
disconnect and idle timeout without writing an audit row. Only explicit API
termination (`session.terminate`) is audited. Session **duration** — how long
privileged access was actually held — is therefore not in the tamper-evident
chain.

**Remediation.** Emit `session.end` with a reason (`disconnect`, `idle_timeout`,
`terminated`) and duration from both paths.

## R-3 — Automation job execution is not audited — CLOSED

**Severity: High.** PCI DSS Req 10.2. **Closed**: `job.start` and `job.finish` are emitted from `runner.execute()` with executor, template, target hosts, exit code and attempt count.

Original finding:

`grep log_action core/services/automation-service` returns nothing. Jobs run
commands as privileged accounts on managed hosts — including `rotate_secret`,
`account_push`, `disable_account` and `remove_account`, which change access
itself — and none of it reaches the audit chain. `credential.secret_issued` now
records that a job *obtained* a credential, which is a partial compensating
control, but not what the job then did with it.

**Remediation.** Audit job submit / start / finish with the executor name,
target hosts and exit status, mirroring `terminal-service/app/audit.py`.

## R-4 — Recording access is unaudited (integrity now protected)

**Severity: Medium.** **Partially closed**: a SHA-256 digest is now taken at ingest and re-checked by `/internal/recordings/{id}/verify`, so tampering is detectable. Playback and presigned-URL issuance are still unaudited — that half remains open.

Original finding:

**Severity: Medium.** SOC 2 CC7; PCI DSS Req 10.5.

Session recordings (`ZARecording`) carry no hash, checksum or signature, so a
recording can be altered or replaced without detection — while the audit log
that *references* it is tamper-evident. Playback and presigned-URL issuance
(`recording-service/app/storage.py:102`, 3600 s TTL) write no audit row, so
"who watched this privileged session" is unanswerable.

**Remediation.** Store a SHA-256 over the frame payload at write time, verify on
read, and audit playback/presign.

## R-5 — The database does not prevent audit tampering — CLOSED

**Severity: Medium.** **Closed**: append-only triggers on Postgres and MySQL reject UPDATE/DELETE (identity-service `022_audit_immutability`), and the chain columns are now in `schema.sql` as well as Alembic, so a migration-less install is no longer chain-less. Residual: a superuser can disable the trigger.

Original finding:

**Severity: Medium.** PCI DSS Req 10.5.

`za_audit_logs` has no `REVOKE UPDATE/DELETE`, no immutability trigger, and the
application's DB role can freely modify it. The hash chain makes tampering
*detectable* — proven by `core/tests/test_audit_chain.py` — but nothing makes it
*impossible*, and detection only happens when someone runs `/audit/verify`.

Also: `core/schema/postgres/schema.sql:144-156` and the MySQL equivalent do not
contain `seq`, `prev_hash` or `entry_hash` at all. Those columns arrive only via
Alembic migration `008_audit_hash_chain.py`, nullable and with no backfill. A
deployment built from `schema.sql` without running migrations has an audit table
with **no chain columns**, and rows written before the migration have null
hashes.

**Remediation.** `REVOKE UPDATE, DELETE ON za_audit_logs` from the application
role, add an `ON UPDATE/DELETE` rule or trigger, fold the chain columns into
`schema.sql`, and schedule `/audit/verify` rather than relying on manual runs.

## R-6 — KEK rotation cannot rotate the primary credential vault — CLOSED

**Severity: High.** **Closed**: rotation now rewraps `wrapped_dek` for envelope rows and re-encrypts `secret_ciphertext` only for legacy rows, with `DRY_RUN` reporting counts per mode.

Original finding:

**Severity: High.** PCI DSS Req 3.6/3.7.

`ops/rotate_vault_key.py:37-45` reads every `za_credentials.secret_ciphertext`
and calls `decrypt_secret(ciphertext, old_kek)`. That is only correct for the
legacy single-KEK rows. Envelope rows — which are the actual PAM vault, per
`inventory-service/app/vault.py:67-73` — encrypt the secret under a random
per-row DEK, and the KEK only wraps that DEK in `wrapped_dek`, a column the
script never selects or writes.

**Verified failure mode** (measured, not inferred): decrypting an envelope
ciphertext with the KEK raises `InvalidTag`, because AES-GCM authenticates. So
the script *crashes on the first envelope row* rather than silently mis-rotating,
and because `commit()` is after the loop the transaction rolls back. There is no
silent corruption and no partial rotation — the operator gets a loud failure.

The control failure is therefore capability, not integrity: **KEK rotation for
the primary credential store is not possible with the shipped tooling.** An
organisation that must rotate after a suspected key compromise cannot do so.

Compounding: ciphertexts carry no key-version or algorithm marker
(`core/libs/dbcore/crypto.py`), so rows cannot be classified by encryption mode
without trial decryption.

**Remediation.** Rewrap `wrapped_dek` under the new KEK in the same transaction
(the DEK itself need not change, which is the point of the hierarchy — only small
blobs are rewritten, not every credential). Add a `--dry-run` reporting row counts
per encryption mode, write an audit row for the rotation, and add a key-version
prefix to the ciphertext format.

## R-7 — Audit forwarding from satellite services is best-effort

**Severity: Medium.** PCI DSS Req 10.

identity-service owns the chain; terminal-service and inventory-service forward
over HTTP. A dropped forward is not a missing log line — the entry never
receives a sequence number, so `verify_chain()` cannot detect its absence. The
chain stays internally consistent while being incomplete.

Partially remediated: forwarders now check response status and log at error
level, and `critical=True` fail-closes credential egress
(`core/tests/test_audit_failclosed.py`). Routine events remain best-effort by
design, to avoid identity-service availability taking down every request.

**Remediation.** Durable local spool with retry, so an entry survives an
identity-service outage instead of being lost.

## R-8 — No lock on the audit chain for non-Postgres/MySQL engines

**Severity: Low.** Only the two supported engines are covered; the `else` branch
in `identity-service/app/audit.py:46` yields without taking any lock. Low today
because no third engine is supported — it becomes High the moment one is added.

**Remediation.** Fail closed on an unrecognised engine rather than proceeding
unlocked.

---

## Out of scope: `modules/jumpserver-legacy/`

Quarantined, not wired into the core stack, slated for deletion as the Phase 2
port lands. Two critical flaws found there were fixed rather than deferred
(missing signature verification on launch-token, `ProxyCommand` shell injection
in playbook-studio). Known remaining issues in that tree:

- `/launch-tokens` mints tokens with no authentication.
- SSH host key verification disabled — `known_hosts: None`
  (`ssh_terminal.py:122,137`) and `StrictHostKeyChecking=no`. Enabling it is not
  a drop-in change: no `known_hosts` is populated, so it needs a trust-on-first-use
  or provisioning path, which belongs with the `SessionBroker` implementation.

Neither is reachable from a `core`-only deployment.
