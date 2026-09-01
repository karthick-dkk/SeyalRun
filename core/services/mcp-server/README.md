# SeyalRun MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) front door that lets AI
agents operate SeyalRun — list hosts, query the audit log, run pre-approved
automation, and more — **within the same permission and audit boundary as every
other caller.**

The MCP server authorizes nothing itself. Every tool and resource forwards the
agent's scoped Personal Access Token to the api-gateway, which enforces
`scopes ∩ role ∩ authorization` and writes the tamper-evident audit row. So an
agent can only ever do a subset of what the human who issued its token can do, and
**it can never read a credential secret.**

```
AI agent ──MCP/JSON-RPC (Bearer sr_…)──▶ mcp-server ──REST (same Bearer)──▶ api-gateway ──▶ services
                                              │                                  │
                                     (no auth of its own)          scopes ∩ role ∩ authz + audit chain
```

## Connecting

- **Endpoint:** `POST https://<your-seyalrun-host>/mcp` (streamable-HTTP, JSON-RPC 2.0).
- **Auth:** `Authorization: Bearer sr_<token>` — a SeyalRun Personal Access Token.
  A request with no bearer token gets `401` with a `WWW-Authenticate` header pointing
  at `/.well-known/oauth-protected-resource`, which names SeyalRun as the token issuer.
- **Get a token:** in the SeyalRun console, **Admin → Security → Personal Access
  Tokens → + Token**, pick the scopes the agent needs, and copy the `sr_…` value
  (shown once). See [Scopes](#scopes).

Point your MCP client at that URL with the token as the bearer credential. Any
streamable-HTTP MCP client works; there is no SeyalRun-specific SDK.

## Scopes

A token is granted a subset of these. The gateway enforces them on every call, so a
tool the token lacks the scope for returns a gateway `403` (surfaced as a tool error).

| Scope | Grants |
|---|---|
| `inventory:read` | list/read hosts, zones, groups |
| `automation:read` | list job-templates and job runs |
| `sessions:read` | read session history / recordings |
| `audit:read` | query the audit log |
| `metrics:read` | read the metrics dashboard |
| `notifications:read` | list notifications |
| `automation:run` | run an **allowlisted** job-template (admin pre-approved) |
| `inventory:write` | create/edit hosts, zones, groups |
| `sessions:open` | open interactive SSH/SFTP sessions |
| `notifications:ack` | acknowledge notifications |

**Never grantable to a token:** `credentials:*` (an agent can *use* a credential to
open a session, if it holds `sessions:open`, but can never read the secret) and
`admin:*` (users, roles, authorizations, token issuance, settings). An empty-scope
token is denied everything.

## Tools

`tools/list` returns 13 tools; each declares the scope it requires.

| Tool | Scope | Does |
|---|---|---|
| `whoami` | *(always available)* | This token's identity, role, and exact scopes — **call this first** |
| `list_hosts` | `inventory:read` | List hosts SeyalRun can broker sessions to |
| `get_host` | `inventory:read` | One host by id |
| `list_zones` | `inventory:read` | Zones / gateway topology |
| `create_host` | `inventory:write` | Register a host (needs ≥1 asset group) |
| `list_automation_templates` | `automation:read` | Available job-templates |
| `list_job_runs` | `automation:read` | Recent runs + status |
| `run_automation` | `automation:run` | Run an allowlisted template against hosts |
| `query_audit` | `audit:read` | Query the tamper-evident audit log |
| `get_metrics` | `metrics:read` | Metrics dashboard |
| `list_sessions` | `sessions:read` | Recorded SSH sessions |
| `list_notifications` | `notifications:read` | Notifications / alerts |
| `ack_notification` | `notifications:ack` | Acknowledge a notification |

## Resources

`resources/list` returns read-only context an agent can pull by URI; each is backed
by the same scope enforcement as the tools.

| URI | Scope |
|---|---|
| `seyalrun://inventory/hosts` | `inventory:read` |
| `seyalrun://inventory/zones` | `inventory:read` |
| `seyalrun://automation/templates` | `automation:read` |
| `seyalrun://audit/recent` | `audit:read` |
| `seyalrun://metrics/dashboard` | `metrics:read` |

## Example

```sh
SR=https://your-seyalrun-host
TOK=sr_your_scoped_token

# initialize
curl -sk -X POST "$SR/mcp" -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'

# who am I / what can I do
curl -sk -X POST "$SR/mcp" -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'

# list hosts (needs inventory:read)
curl -sk -X POST "$SR/mcp" -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_hosts","arguments":{}}}'

# run an allowlisted playbook (needs automation:run; 403 -> tool error otherwise)
curl -sk -X POST "$SR/mcp" -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"run_automation","arguments":{"template_id":"<id>","host_ids":["<host>"]}}}'
```

## Design notes

- **One security path.** The MCP server is a stateless translator: MCP tool/resource
  call → gateway REST call carrying the agent's PAT. There is no parallel auth or
  authorization logic to drift out of sync with the console or the raw API.
- **Least privilege by construction.** Effective access is `token scopes ∩ owner
  role ∩ per-host authorization`. Scopes only ever narrow; they can't widen.
- **No shell for agents.** Interactive SSH is not exposed as a tool. The safe "act on
  a host" primitive for an agent is `run_automation` — an admin-approved playbook with
  server-filtered parameters, fully recorded. (`sessions:open` governs interactive,
  human-driven sessions.)
- **Everything is audited.** Because calls land on the gateway, each one produces the
  same audit-chain row a console or API caller would.

## Configuration

| Env | Default | Purpose |
|---|---|---|
| `GATEWAY_URL` | `http://api-gateway:8000` | Internal api-gateway base URL |

Runs on port `8110`; the edge-proxy routes `/mcp` to it. Health at `GET /health`.
