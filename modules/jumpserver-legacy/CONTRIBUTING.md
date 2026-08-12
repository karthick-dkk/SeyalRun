# Contributing to SeyalRun

Thank you for your interest in contributing!

## Development Setup

### Backend (Playbook Studio)

```bash
cd services/playbook-studio
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi uvicorn sqlalchemy asyncpg alembic pydantic-settings asyncssh \
            ruamel.yaml aiosmtplib structlog httpx redis ansible-core
```

### Frontend (SeyalRun Console)

```bash
cd services/pravesh-console
npm install
npm run dev   # http://localhost:3000 with proxy to http://localhost:8005
```

## Pull Request Guidelines

- One feature/fix per PR
- Add a clear description of what changed and why
- Backend: follow existing patterns (FastAPI routers, SQLAlchemy async, Pydantic v2)
- Frontend: Vue 3 Composition API, TypeScript, no external UI libraries
- Keep the dark terminal aesthetic (tokens in `src/assets/tokens.css`)

## Reporting Issues

Please include:
- SeyalRun version
- JumpServer version
- Host architecture (ARM64 / amd64)
- `docker logs ps_service --tail 100`
- Steps to reproduce
