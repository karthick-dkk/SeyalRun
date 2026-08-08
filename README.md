# [SeyalRun](https://seyalrun.com/)

A standalone PAM and Automation platform for your server fleet. 

<img width="2834" height="1630" alt="image" src="https://github.com/user-attachments/assets/68872c8c-c21c-4da1-a950-c42e9ea68190" />

## Quick Deploy

Pulls prebuilt images from Docker Hub (`docker.io/karthickdk02/seyalrun-*`) —
no source checkout, no local build required.

```sh
curl -fsSL https://raw.githubusercontent.com/karthick-dkk/seyalrun_zabbix/main/install.sh | bash
```

This installs into `./seyalrun`, generates a `.env` with random secrets, a
self-signed TLS cert, brings up a Dockerized Postgres, runs migrations, seeds
the superadmin account, and starts every service. Takes a few minutes on
first run (image pulls); safe to re-run (an existing `.env`/cert/database is
reused, not overwritten).
<img width="2842" height="1622" alt="image" src="https://github.com/user-attachments/assets/1ca5e5cf-fad5-4964-8224-0ac56c62bff8" />

# Repository layout

SeyalRun is a standalone SSH PAM + Automation platform. Integrations with other
systems are optional modules, not part of the product's core.

```
core/         the platform — libs, services, schema, tests, monitoring
modules/      optional integrations
  zabbix/     Zabbix frontend module (embeds SeyalRun's UI inside Zabbix)
  jumpserver-legacy/   staging for the JumpServer merge; see its MIGRATION.md
ops/          deploy, backup, rotation and verification scripts
security/     scanning rules enforced by CI
```

`core/` runs on its own with no module enabled. Extensibility goes through
`core/libs/pluginbase`: each axis is a small ABC, and implementations are
discovered from `app/plugins/<axis>/` at startup — so a deployment that does not
use an integration never even imports its code.

# Documentation
Please refer to [seyalrun.com](https://seyalrun.com/)

Every choice has a default and can be overridden by exporting a variable
before running the command above:

| Variable | Default | Purpose |
|---|---|---|
| `SEYALRUN_DIR` | `./seyalrun` | Where to install |
| `SEYALRUN_HOST` | auto-detected | Hostname/IP for the TLS cert + `FRONTEND_ORIGIN` |
| `FRAME_ANCESTORS` | *(none)* | Set to your Zabbix origin to allow iframe embedding |
| `SEYALRUN_DB_ENGINE` | `postgres` | `postgres` or `mysql` |
| `SEYALRUN_DB_HOST` | *(unset — uses Dockerized DB)* | Point at an **existing** Postgres/MySQL instead — see below |

**Using your own (local/bare-metal) database instead of the Dockerized
one** — set `SEYALRUN_DB_HOST` (plus user/password); the installer creates
the four required databases (`seyalrun_identity`, `seyalrun_inventory`,
`seyalrun_terminal`, `seyalrun_automation`) and imports the schema on your
instance instead of starting a database container:

```sh
SEYALRUN_DB_HOST=192.168.64.8 \
SEYALRUN_DB_USER=seyalrun \
SEYALRUN_DB_PASSWORD='<your-db-password>' \
SEYALRUN_DB_ENGINE=postgres \
  curl -fsSL https://raw.githubusercontent.com/karthick-dkk/seyalrun_zabbix/main/install.sh | bash
```

`SEYALRUN_DB_USER` must be able to create databases on that instance.
`SEYALRUN_DB_PORT` defaults to `5432` (postgres) / `3306` (mysql);
`SEYALRUN_DB_SSLMODE` defaults to `require`.

Building from source instead (for development, or to modify the code) —
see [Quickstart](#quickstart) below, which uses `docker compose ... --build`
against this checkout. The same local-DB setup (`ops/init-db.sh`) is shared
by both paths.


- **edge-proxy** is the *only* service published to the host (HTTP redirect
  on `EDGE_HTTP_PORT`, TLS on `EDGE_HTTPS_PORT`).
- **identity-service** and **inventory-service** are internal-only — every
  call from api-gateway carries a short-lived `X-Service-Token` (HS256,
  `SERVICE_JWT_SECRET`) that they verify before doing any work.
- **redis** backs api-gateway's per-IP/user rate limiting.
- **Self-monitoring is agentless**: Zabbix polls one aggregate HTTP endpoint
  (`/webhook/zabbix/monitor`, served by zabbix-integration-service through
  edge-proxy) that fans out to every service's `/health` and `/metrics` —
  no sidecar container and no Docker-socket exposure.


## Quickstart

1. **Configure `.env`**

   ```sh
   cp .env.example .env
   ```

   Fill in every blank value — see comments in `.env.example`. Generate
   secrets with e.g. `openssl rand -hex 32` (`JWT_SECRET`,
   `SERVICE_JWT_SECRET`, `API_TOKEN_PEPPER`, `ZA_VAULT_PASSWORD`,
   `ZABBIX_WEBHOOK_HMAC_SECRET`) and `openssl rand -hex 16` for
   `ZA_VAULT_SALT`. Set `TLS_CERT_PATH`/`TLS_KEY_PATH` to a cert/key pair
   (self-signed is fine for staging).

2. **Database** — choose one:

   - **Bare-metal Postgres/MySQL (recommended)**: set `DB_ENGINE`,
     `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE` to point
     at your existing instance, then:

     ```sh
     ops/init-db.sh
     ```

     This creates all four databases (`seyalrun_identity`,
     `seyalrun_inventory`, `seyalrun_terminal`, `seyalrun_automation`) and
     imports `core/schema/<engine>/schema.sql` into identity/inventory
     (idempotent, safe to re-run) — `terminal`/`automation` have no static
     schema, their tables come entirely from step 4's Alembic migrations.

   - **Dockerized Postgres/MySQL** (no bare-metal DB available): set
     `DB_HOST=postgres` (or `mysql`), then bring up the matching overlay
     profile — its `docker-entrypoint-initdb.d` scripts create all four
     databases automatically:

     ```sh
     docker compose -f docker-compose.yml -f docker-compose.db.yml --profile postgres-db up -d postgres
     # then run ops/init-db.sh against DB_HOST=postgres to import the schema
     ```

3. **Build and start the stack**

   ```sh
   docker compose up -d --build
   # (Dockerized DB): add -f docker-compose.db.yml --profile postgres-db
   ```

4. **Run Alembic migrations** for every service (idempotent, safe to re-run
   — `ops/init-db.sh` only creates the databases and imports the identity/
   inventory schema; every service's tables come from its own migrations):

   ```sh
   for svc in identity-service inventory-service terminal-service automation-service \
              recording-service zabbix-integration-service metrics-service; do
     docker compose run --rm --no-deps "$svc" python -m alembic upgrade head
   done
   ```

5. **Seed the superadmin user**:

   ```sh
   docker compose run --rm --no-deps identity-service python -m app.seed
   ```

   If `SEED_ADMIN_PASSWORD` is unset in `.env`, a random password is
   generated and printed **once** — save it immediately.

6. Open `https://<host>:${EDGE_HTTPS_PORT:-8443}/` and log in.

## `.env` reference

Every variable is documented in [.env.example](.env.example). Highlights:

| Variable | Purpose |
|---|---|
| `DB_ENGINE`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE` | Single engine for both `seyalrun_identity` and `seyalrun_inventory` |
| `JWT_SECRET` | Client session JWT (identity-service issues, api-gateway verifies) |
| `SERVICE_JWT_SECRET` | Service-to-service `X-Service-Token` (api-gateway -> identity/inventory) |
| `API_TOKEN_PEPPER` | Extra pepper hashed into Personal Access Tokens |
| `ZA_VAULT_PASSWORD` / `ZA_VAULT_SALT` | AES-256-GCM credential encryption key material (scrypt-derived) |
| `TLS_CERT_PATH` / `TLS_KEY_PATH` | edge-proxy TLS cert/key (host paths, bind-mounted) |
| `FRONTEND_ORIGIN` | CORS allow-origin for api-gateway — must match the URL you browse to |
| `SEED_ADMIN_USERNAME` / `SEED_ADMIN_PASSWORD` | Initial superadmin (leave password blank to auto-generate) |
| `ZABBIX_MODULE_SECRET` | Optional — only if using [modules/zabbix/seyalrun/](modules/zabbix/README.md); HMAC-signs the module's SSO handshake |

No secret has a default value — services fail fast at startup if a required
var is missing. `.env` is git-ignored; never commit real values or hardcode
them in source (`core/libs/securelog` redacts `password`/`secret`/`token`/`vault`/
`authorization` fields from all structured logs regardless).

## Self-monitoring (Zabbix)

Every Phase-1 service exposes `GET /health` and `GET /metrics` — both JSON.
`/metrics` is a flat `core/libs/obsmetrics` snapshot (`requests_total`,
`errors_total`, `uptime_seconds`, plus optional service extras) built for
Zabbix HTTP polling + JSONPath preprocessing. Monitoring is agentless: zabbix-integration-service
serves `GET /webhook/zabbix/monitor` (through edge-proxy, authenticated by
the `X-Monitor-Token` header carrying `ZABBIX_WEBHOOK_HMAC_SECRET`), which
concurrently aggregates every service's health + metrics into one JSON
payload. The template polls it with a single HTTP-agent master item; all
discovery and per-service items are dependent JSONPath slices of that
payload — one HTTP request per interval, regardless of service count.

**Import steps:**

1. In Zabbix, import the template matching your server version:
   - Zabbix 7.0: `core/monitoring/zabbix-templates/7.0/seyalrun-platform.yaml`
   - Zabbix 8.0: `core/monitoring/zabbix-templates/8.0/seyalrun-platform.yaml`
2. Create (or pick) a host for the SeyalRun stack and link the imported
   **SeyalRun Platform** template (no agent interface needed).
3. Set the template macros on that host:
   - `{$SEYALRUN.MONITOR.URL}` = `https://<edge-host>:<EDGE_HTTPS_PORT>/webhook/zabbix/monitor`
   - `{$SEYALRUN.MONITOR.TOKEN}` = the stack's `ZABBIX_WEBHOOK_HMAC_SECRET`
     (secret-text macro)
4. Within a minute the master item polls the endpoint; discovery then
   populates health, request-rate, error-rate, and uptime items for every
   service. Error-log tailing (an agent-only capability) was retired with
   the sidecar — 5xx rates from `/metrics` cover the alerting need.

## SeyalRun inside Zabbix (frontend module)

Separately from monitoring, `modules/zabbix/seyalrun/` is a Zabbix **frontend
module** that embeds SeyalRun directly into the Zabbix UI: a SeyalRun menu
right after Monitoring (Dashboard, Assets, SSH Hosts, Sessions, Jobs,
Automation, Trigger Bindings), a permission-aware SSH-Hosts page with a
one-click terminal icon on hosts you can write to, and a SeyalRun Settings
page under Administration. See **[modules/zabbix/README.md](modules/zabbix/README.md)**
for install steps, the `ZABBIX_MODULE_SECRET` trust setup, and troubleshooting.

