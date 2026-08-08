# SeyalRun Architecture

## Request Flow

```
Browser
  └── GET http://HOST:3000/*
        └── Nginx (pravesh-console container)
              ├── Static files (Vue SPA dist/)
              ├── /api/* → proxy_pass http://ps_service:8005
              └── /ws/*  → proxy_pass ws://ps_service:8005

ps_service (Playbook Studio)
  ├── FastAPI REST API
  │     ├── Auth: validate Bearer token → JumpServer /api/v1/users/profile/
  │     │         (cached in Redis 300s)
  │     ├── /api/v1/modules       → static Python catalog
  │     ├── /api/v1/templates     → static Python catalog
  │     ├── /api/v1/playbooks     → PostgreSQL CRUD
  │     ├── /api/v1/jobs          → PostgreSQL + asyncio subprocess
  │     ├── /api/v1/ssh/sessions  → PostgreSQL CRUD
  │     ├── /api/v1/assets        → JumpServer proxy
  │     └── /api/v1/dashboard     → aggregated DB queries + JMS proxy
  │
  ├── WebSocket: /ws/ssh/{session_id}
  │     ├── Validates token via Redis cache
  │     ├── Opens asyncssh connection to target host
  │     ├── Bidirectional: browser input → SSH stdin, SSH stdout → browser
  │     ├── Session recording: timestamped frames in PostgreSQL
  │     └── On WS disconnect: moves connection to idle pool (configurable timeout)
  │
  ├── WebSocket: /ws/jobs/{id}/stream
  │     └── Redis pub/sub → browser (job output lines)
  │
  └── Ansible subprocess
        ├── Writes playbook YAML to /playbooks/studio/{uuid}.yml
        ├── ansible-playbook -i HOST, playbook.yml --extra-vars ...
        ├── Streams stdout line by line → Redis PUBLISH → WS → browser
        └── Cleans up temp file on completion
```

## Database Schema

All tables in `playbook_studio` schema (isolated from JumpServer).

```
playbooks          jobs               ssh_sessions
─────────          ────               ────────────
id (UUID PK)       id (UUID PK)       id (UUID PK)
name               playbook_id (FK)   user
description        status             asset_address
category           triggered_by       ssh_username
tags (JSONB)       inventory_selector status (active/idle/closed/error)
tasks (JSONB)      extra_vars (JSONB) started_at
variables (JSONB)  started_at         ended_at
yaml_cache         finished_at        duration_seconds
is_template        duration_seconds   recording (JSONB) [{t,d} frames]
created_by         exit_code          command_count
created_at         output_lines (JSON)
updated_at         yaml_content

alert_rules        alert_history      notification_channels
───────────        ─────────────      ─────────────────────
id                 id                 id
name               rule_id (FK)       name
event_type         event_type         channel_type
conditions (JSONB) event_payload      config (JSONB)
channels (JSONB)   delivery_status    is_active
enabled            delivered_at
```

## SSH Session Lifecycle

```
CREATE session (DB: pending)
   ↓
Browser opens WebSocket /ws/ssh/{id}?token=...
   ↓
Backend: asyncssh.connect() → SSH handshake
   ↓
DB: status = active
   ↓
Bidirectional forwarding (read_ssh ↔ read_ws tasks)
   ↓
[Browser navigates away → WS closes]
   ↓
Backend: moves to idle pool (asyncssh stays alive!)
DB: status = idle
   ↓
[Idle timer: 15 min default]
   ├─ User returns → WS reconnects → replay buffer → resume
   └─ Timer fires → conn.close() → DB: status = closed
```
