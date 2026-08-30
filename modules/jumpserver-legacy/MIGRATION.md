# JumpServer legacy tree — disposition

This directory is the former `SeyalRun_JS` repository, merged with its full
history via `git subtree` at the Phase 0 hardening commit. **Nothing here is
wired into the core stack.** It is staging for the Phase 2 port, and it will be
empty when that port completes.

Do not add features here. Fixes that keep an existing deployment alive are
fine; anything else belongs in `core/` behind the relevant plugin axis.

## Why this still exists after Phase 1

The merge plan had Phase 1 delete the redundant services outright. That order
was inverted deliberately: their replacements do not land until Phase 2, and
deleting a service before its replacement exists would break any running
`core + JumpServer` deployment — exactly the flag-day cutover the plan set out
to avoid. Each service is therefore retired **as its replacement lands**, not
before.

## Disposition

| Component | Verdict | Replacement |
|---|---|---|
| `services/webhook-receiver` | Deleted ✓ | `core/services/zabbix-integration-service` already does HMAC + IP allowlist + rate limiting + replay protection + pre-bound dispatch, and does not fail open |
| `services/zbx-sync` | Port, then delete | `run_sync()` host-diff logic → `SessionBroker.sync_targets()` on the JumpServer broker, driven by inventory-service |
| `services/launch-token` | Port, then delete | `SessionBroker.mint_launch_token()`. The Redis one-use JTI logic is sound and should be carried over; the token signing must move onto `libs/servicetoken` conventions |
| `services/playbook-studio` (API, catalog, builder) | Port, then delete | `core/services/automation-service` as an `ActionExecutor` plugin, plus `za_playbook_*` tables |
| `services/playbook-studio/app/ws/ssh_terminal.py`, `services/ssh_pool.py` | Port, then delete | `core/services/terminal-service/app/plugins/brokers/jumpserver_broker.py`. Must not remain a second live SSH path |
| `services/playbook-studio/app/dependencies.py` | Port, then delete | `core/services/identity-service/app/plugins/idp/jumpserver.py` — model it on the existing `zabbix_sso.py` |
| `services/automation-bridge` | Keep, relocate | Becomes the automation execution sidecar under `modules/jumpserver/`, reached over a service-token-authenticated internal API |
| `services/pravesh-console` | Fold in | Same Vue 3 stack as `core/services/frontend`; two SPAs would double the CSP, routing and auth surface. The CodeMirror playbook editor becomes a route there |
| `services/_shared/pravesh_shared` | Delete last | Superseded by `core/libs`. `circuit_breaker.py` is already ported to `core/libs/resilience`; the rest goes once no service here imports it |

## Carried forward already

- Phase 0 security fixes are in this tree (TLS verification on every outbound
  client, no fail-open webhook HMAC, no weak signing-secret default).
- `pravesh_shared/circuit_breaker.py` → `core/libs/resilience`, with Prometheus
  coupling replaced by injected callbacks so `core/libs` stays stdlib-only.

## Naming

The code here uses the internal name "Pravesh" (`pravesh_shared`, `PS_`/`LT_`/
`WR_`/`ZS_` env prefixes, `X-Pravesh-Signature`, `ps_service`/`ab_service`
container names). Everything retires with the tree; nothing ported into `core/`
should carry it forward.
