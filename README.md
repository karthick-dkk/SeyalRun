# SeyalRun — Open Source PAM & DevOps Automation Console

> **Privileged Access Management + Visual Ansible Automation + Web SSH Terminal**
> Built on top of JumpServer — zero modifications to upstream projects.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![Vue](https://img.shields.io/badge/Vue-3.4-green)](https://vuejs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688)](https://fastapi.tiangolo.com)

---

## What is SeyalRun?

SeyalRun is a self-hosted DevOps operations console that adds a modern UI layer on top of [JumpServer](https://www.jumpserver.org/). It provides:

- 🖥 **Web SSH Terminal** — Multi-tab, split-screen, session recording & replay
- 📋 **Visual Playbook Builder** — Form-based Ansible playbook creation with 30 modules
- 📚 **Template Library** — 20 pre-built Linux admin templates
- 🚀 **One-click Execution** — Run Ansible on any managed host from the browser
- 📊 **Dashboard** — Assets, SSH logins, failed attempts, job history, activity graph
- 🔔 **Alerts** — Webhook + email notifications for failures and thresholds
- 🔒 **JumpServer Auth** — Uses your existing JumpServer login


---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Browser                                      │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │          SeyalRun Console  (Vue 3 SPA)  — port 3000             │    │
│  │   Dashboard │ Assets │ Terminal │ Playbooks │ Templates │ Jobs  │    │
│  └────────────────────────┬────────────────────────────────────────┘    │
└───────────────────────────┼─────────────────────────────────────────────┘
                            │ HTTP/WebSocket
                     ┌──────▼──────┐
                     │    Nginx    │  (reverse proxy inside container)
                     └──────┬──────┘
                            │ /api/* and /ws/*
              ┌─────────────▼──────────────────┐
              │      Playbook Studio            │
              │      FastAPI — port 8005        │
              │  ┌──────────────────────────┐   │
              │  │  REST API + WebSocket    │   │
              │  │  Ansible Core (embedded) │   │
              │  │  asyncssh SSH tunneling  │   │
              │  └──────────┬───────────────┘   │
              └─────────────┼───────────────────┘
           ┌────────────────┼──────────────────────┐
           │                │                      │
    ┌──────▼──────┐  ┌──────▼──────┐  ┌───────────▼───────────┐
    │ PostgreSQL  │  │   Redis     │  │     JumpServer        │
    │ DB: playb.. │  │   DB5       │  │  (auth + assets)      │
    └─────────────┘  └─────────────┘  └───────────────────────┘
                                              │
                              ┌───────────────┼──────────────────┐
                              │               │                  │
                       ┌──────▼──┐    ┌───────▼───┐    ┌────────▼────┐
                       │ Host 1  │    │ Gateway   │    │  Host 2     │
                       │(direct) │    │(bastion)  │    │(via gateway)│
                       └─────────┘    └───────────┘    └─────────────┘
```

### Components

| Component | Tech | Purpose |
|-----------|------|---------|
| `seyalrun-console` | Vue 3 + Vite + xterm.js | Web UI |
| `playbook-studio` | Python FastAPI + asyncssh | API + SSH + Ansible |
| PostgreSQL 16 | Shared with JumpServer | Persistent storage |
| Redis 7 | Shared with JumpServer | Cache + streaming |
| JumpServer CE | v4.10+ | Asset mgmt + auth |


---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Docker + Docker Compose | 24+ |
| JumpServer CE | v4.10+ (running) |
| Architecture | `aarch64` (ARM64) or `amd64` |
| RAM | 2 GB free (above JumpServer) |
| Node.js (build only) | 20+ |

> JumpServer must already be running. SeyalRun reuses its PostgreSQL and Redis.

---

## Quick Start (One Command)

```bash
git clone https://github.com/your-org/seyalrun.git
cd seyalrun
chmod +x scripts/setup-seyalrun.sh

# Replace with your JumpServer host IP
./scripts/setup-seyalrun.sh 192.168.64.2
```

The script auto-detects credentials, creates the database, builds images, runs migrations, and starts both services.

**Access:** `http://YOUR_HOST:3000` — login with your JumpServer credentials.

---

## Manual Deployment

### 1. Create the database

```bash
docker exec jms_postgresql psql -U postgres << 'SQL'
CREATE DATABASE playbook_studio;
CREATE USER seyalrun WITH PASSWORD 'CHANGE_ME_DB_PASS'; -- pragma: allowlist secret
GRANT ALL PRIVILEGES ON DATABASE playbook_studio TO seyalrun;
SQL
docker exec jms_postgresql psql -U postgres -d playbook_studio \
  -c "GRANT ALL ON SCHEMA public TO seyalrun;"
```

### 2. Build the Vue SPA

```bash
cd services/pravesh-console
npm install && npm run build
cd ../..
```

### 3. Build and start Playbook Studio

```bash
docker build -t seyalrun/playbook-studio:latest services/playbook-studio/

# Run migrations  # pragma: allowlist secret
docker run --rm --network jms_net \
  -e PS_DATABASE_URL="postgresql+asyncpg://seyalrun:CHANGE_ME_DB_PASS@jms_postgresql:5432/playbook_studio" \
  -e PS_REDIS_URL="redis://:REDIS_PASS@jms_redis:6379/5" \
  -e PS_JUMPSERVER_API_URL="http://192.168.64.2" \
  seyalrun/playbook-studio:latest python -m alembic upgrade head

# Start
docker run -d --name ps_service --restart unless-stopped \
  --network jms_net -p 127.0.0.1:8005:8005 \
  -v /playbooks:/playbooks \
  -e PS_DATABASE_URL="postgresql+asyncpg://seyalrun:CHANGE_ME_DB_PASS@jms_postgresql:5432/playbook_studio" \
  -e PS_REDIS_URL="redis://:REDIS_PASS@jms_redis:6379/5" \
  -e PS_JUMPSERVER_API_URL="http://192.168.64.2" \
  seyalrun/playbook-studio:latest
```

### 4. Start the Console UI

```bash
docker run -d --name console_ui --restart unless-stopped \
  --network jms_net -p 192.168.64.2:3000:3000 \
  -v $(pwd)/services/pravesh-console/dist:/usr/share/nginx/html:ro \
  -v $(pwd)/services/pravesh-console/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  nginx:alpine
```

### 5. Verify

```bash
curl http://localhost:8005/health
# → {"status":"ok","service":"playbook-studio"}

curl http://192.168.64.2:3000/api/v1/modules | python3 -c \
  "import sys,json; print('Modules:', json.load(sys.stdin)['total'])"
# → Modules: 30
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PS_DATABASE_URL` | ✓ | — | PostgreSQL (asyncpg format) |
| `PS_REDIS_URL` | ✓ | — | Redis (use DB 5) |
| `PS_JUMPSERVER_API_URL` | ✓ | — | JumpServer base URL |
| `PS_LOG_LEVEL` | | `info` | `debug` / `info` / `warning` |
| `PS_SMTP_HOST` | | `localhost` | SMTP for email alerts |
| `PS_SMTP_PORT` | | `587` | SMTP port |
| `PS_SMTP_FROM_ADDRESS` | | — | Alert sender address |
| `PS_SMTP_USE_TLS` | | `true` | Use TLS |
| `PS_SMTP_START_TLS` | | `true` | Use STARTTLS |

---

## Usage

### SSH Terminal

1. **Assets** → Click **⊙ SSH** on any host
2. Select a JumpServer account or enter credentials
3. **⊙ Open Terminal** → opens in a new browser window

**Multi-session keyboard shortcuts:**

| Key | Action |
|-----|--------|
| `Ctrl+Shift+T` | New session tab |
| `Ctrl+Shift+W` | Close current tab |
| `Ctrl+Shift+D` | Toggle split screen |
| `ESC` | Close any dialog |

**Session persistence:** Sessions survive browser navigation. Configure idle timeout in **Settings** (default: 15 min). Idle sessions show **↺ Resume** in the Sessions page.

### Running Playbook Templates

1. **Templates** → Click **▶ Run** on any template
2. Select target hosts from JumpServer asset list
3. Enter SSH credentials (or select stored JumpServer accounts)
4. Click **▶ Run Now** → live output streams to browser

### Building Custom Playbooks

1. **Playbooks** → **+ New Playbook**
2. **+ Add Task** → pick from 30 modules by category
3. Configure parameters via form → live YAML preview
4. Save → Execute

### Session Recording & Replay

- All SSH sessions recorded automatically
- **Sessions** page → click **▶ Replay**
- Speed control: 0.5× to 10×

---

## Project Structure

```
seyalrun/
├── services/
│   ├── playbook-studio/        # FastAPI backend (Python)
│   │   ├── app/
│   │   │   ├── api/            # REST endpoints
│   │   │   ├── catalog/        # 30 modules + 20 templates (static)
│   │   │   ├── models/         # ORM + Pydantic schemas
│   │   │   ├── services/       # Ansible execution, SSH pool, alerts
│   │   │   └── ws/             # WebSocket: SSH terminal + job stream
│   │   ├── migrations/         # Alembic migrations
│   │   └── Dockerfile
│   │
│   └── pravesh-console/        # Vue 3 SPA
│       ├── src/
│       │   ├── components/     # SSH pane, playbook builder, layout
│       │   ├── views/          # Page components
│       │   ├── stores/         # Auth, UI (Pinia)
│       │   ├── composables/    # useEscapeKey, useJobStream
│       │   └── api/            # Axios API client
│       ├── nginx.conf
│       └── Dockerfile
│
├── docker/
│   ├── compose.studio.yml      # SeyalRun services
│   └── compose.sidecars.yml    # Automation bridge + sidecars
│
├── scripts/
│   ├── setup-seyalrun.sh       # Full fresh deployment
│   └── deploy-studio.sh        # Update existing deployment
│
└── docs/
    ├── ARCHITECTURE.md
    └── API.md
```

---

## API Reference

Interactive docs at `http://HOST:8005/docs`

```
GET  /api/v1/health                   Health check
GET  /api/v1/modules                  30 Ansible modules
GET  /api/v1/templates                20 playbook templates
POST /api/v1/templates/{slug}/run     Run template on hosts
GET  /api/v1/playbooks                Custom playbooks list
POST /api/v1/jobs                     Execute a playbook
GET  /api/v1/jobs/{id}                Job status + output
WS   /ws/jobs/{id}/stream             Live job output
POST /api/v1/ssh/sessions             Create SSH session
WS   /ws/ssh/{session_id}             SSH terminal
GET  /api/v1/ssh/sessions             Session history
GET  /api/v1/assets                   JumpServer assets (proxied)
GET  /api/v1/dashboard                Aggregated dashboard stats
GET  /api/v1/settings                 App settings
PATCH /api/v1/settings                Update settings
```

---

## Troubleshooting

**Studio offline in Settings**
```bash
docker logs ps_service --tail 50
curl http://localhost:8005/health
```

**SSH won't connect**
```bash
docker exec ps_service nc -zv TARGET_IP 22
docker exec ps_service python3 -c "import asyncssh; print(asyncssh.__version__)"
```

**Module not found (collection error)**
```bash
docker exec ps_service ansible-galaxy collection list | grep -E "posix|general|crypto"
```

**Database migration error**
```bash
docker run --rm --network jms_net \
  -e PS_DATABASE_URL="your-url" \
  seyalrun/playbook-studio:latest \
  python -m alembic upgrade head
```

---

## Security

- No credential storage — auth delegated entirely to JumpServer API
- SSH passwords cleared from DB immediately after WebSocket connects
- Alert webhooks signed with HMAC-SHA256 (`X-SeyalRun-Signature`)
- Bearer tokens cached in Redis only (5 min TTL)

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

---

## Contributing

1. Fork → feature branch → PR
2. Backend: Python 3.12, FastAPI, SQLAlchemy async
3. Frontend: Vue 3, TypeScript, no external UI component libraries
4. Run linting before PR: `ruff check` (Python), `vue-tsc` (TypeScript)

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

[JumpServer](https://github.com/jumpserver/jumpserver) · [asyncssh](https://github.com/ronf/asyncssh) · [xterm.js](https://github.com/xtermjs/xterm.js) · [FastAPI](https://fastapi.tiangolo.com) · [Vue 3](https://vuejs.org)
