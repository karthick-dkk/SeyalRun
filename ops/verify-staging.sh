#!/bin/bash
# Post-deploy verification for a SeyalRun v2.0 Phase 1 staging host.
# Run automatically at the end of ops/deploy-staging.sh, or standalone:
#
#   ops/verify-staging.sh <HOST>
#
# Environment:
#   SSH_USER                 - remote SSH user (default: test)
#   SUDO_PASSWORD            - remote sudo password for docker checks (see
#                               ops/deploy-staging.sh). If unset, docker-based
#                               checks are skipped.
#   SEYALRUN_ADMIN_PASSWORD  - seeded superadmin password, enables the
#                               login/PAT/Security-tab CRUD checks. If unset,
#                               those checks are skipped.
#
# Prints a ✓/✗ checklist and exits non-zero if any check failed.

set -uo pipefail

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  echo "Usage: $0 <HOST>  — e.g. $0 192.168.64.2" >&2
  exit 1
fi
SSH_USER="${SSH_USER:-test}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
REMOTE_DIR="/opt/seyalrun-v2"
# Read ports from the remote .env so the check works even when defaults are changed
EDGE_HTTPS_PORT=$(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "grep '^EDGE_HTTPS_PORT=' ${REMOTE_DIR}/.env 2>/dev/null | cut -d= -f2" 2>/dev/null)
EDGE_HTTP_PORT=$(ssh  "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "grep '^EDGE_HTTP_PORT='  ${REMOTE_DIR}/.env 2>/dev/null | cut -d= -f2" 2>/dev/null)
EDGE_HTTPS_PORT="${EDGE_HTTPS_PORT:-8443}"
EDGE_HTTP_PORT="${EDGE_HTTP_PORT:-8080}"
EDGE_HTTPS="https://${HOST}:${EDGE_HTTPS_PORT}"

pass=0; total=0
ok()   { printf '  \033[0;32m\xe2\x9c\x93\033[0m %s\n' "$1"; pass=$((pass+1)); total=$((total+1)); }
bad()  { printf '  \033[0;31m\xe2\x9c\x97\033[0m %s\n' "$1"; total=$((total+1)); }
note() { printf '  \033[0;36m.\033[0m %s\n' "$1"; }

json_get() {
  python3 -c "import sys,json
try:
    print(json.load(sys.stdin).get('$1',''))
except Exception:
    print('')"
}

# Runs "$1" as root on the remote host. Empty output (and non-zero exit)
# if SUDO_PASSWORD is unset and passwordless sudo isn't configured.
remote_sudo_out() {
  if [[ -n "${SUDO_PASSWORD:-}" ]]; then
    ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "sudo -S bash -c '$1'" <<< "$SUDO_PASSWORD" 2>/dev/null
  else
    ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "sudo -n bash -c '$1'" 2>/dev/null
  fi
}

echo "=== SeyalRun v2.0 staging verification: ${HOST} ==="

# ── Edge proxy ────────────────────────────────────────────────────────────
# Follow the redirect, don't just count it. Checking for a 301 alone passed for
# months while the Location pointed at :443, where nothing listens — the check
# said "redirects HTTP -> HTTPS" about a link that dead-ended (ISSUE-001). Same
# failure shape as R-9: a proxy for the property, measured instead of the
# property. Assert the browser actually arrives.
code=$(curl -s -o /dev/null -w '%{http_code}' "http://${HOST}:${EDGE_HTTP_PORT}/" 2>/dev/null || echo 000)
if [[ "$code" != "301" ]]; then
  bad "edge-proxy :${EDGE_HTTP_PORT} returned ${code} (want 301)"
else
  final=$(curl -skL -o /dev/null -w '%{url_effective} %{http_code}' --max-time 15 \
            "http://${HOST}:${EDGE_HTTP_PORT}/" 2>/dev/null || echo "- 000")
  [[ "$final" == "https://"*" 200" ]] \
    && ok "edge-proxy :${EDGE_HTTP_PORT} redirects HTTP -> HTTPS and lands (${final})" \
    || bad "edge-proxy :${EDGE_HTTP_PORT} redirect does not resolve: ${final}"
fi

health_json=$(curl -sk --max-time 10 "${EDGE_HTTPS}/api/v1/health" 2>/dev/null || echo '{}')
echo "$health_json" | grep -q '"status":"ok"' \
  && ok "GET /api/v1/health -> ok (${health_json})" \
  || bad "GET /api/v1/health did not return ok: ${health_json}"

code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "${EDGE_HTTPS}/" 2>/dev/null || echo 000)
[[ "$code" == "200" ]] \
  && ok "frontend served at / (200)" \
  || bad "/ returned ${code} (want 200)"

# ── Internal services not host-reachable ─────────────────────────────────────
for port in 8101 8102 8103 8104 8105 8106 8107 8000 6379; do
  if curl -s --max-time 2 "http://${HOST}:${port}/health" >/dev/null 2>&1; then
    bad "port ${port} is reachable from outside (should be internal-only)"
  else
    ok "port ${port} not reachable from outside (internal-only)"
  fi
done

# ── docker compose / Alembic (requires sudo on the host) ─────────────────────
COMPOSE_FLAGS=$(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "tr '\n' ' ' < ${REMOTE_DIR}/.compose-flags" 2>/dev/null)
if [[ -n "$COMPOSE_FLAGS" ]]; then
  ps_out=$(remote_sudo_out "cd ${REMOTE_DIR} && docker compose ${COMPOSE_FLAGS} ps --format \"{{.Service}} {{.State}} {{.Health}}\"")
  if [[ -n "$ps_out" ]]; then
    while read -r svc state health; do
      [[ -z "$svc" ]] && continue
      if [[ "$health" == "healthy" || ( -z "$health" && "$state" == "running" ) ]]; then
        ok "container ${svc}: ${state}${health:+ (${health})}"
      else
        bad "container ${svc}: ${state}${health:+ (${health})}"
      fi
    done <<< "$ps_out"

    for service in identity-service inventory-service terminal-service recording-service automation-service zabbix-integration-service metrics-service; do
      alembic_out=$(remote_sudo_out "cd ${REMOTE_DIR} && docker compose ${COMPOSE_FLAGS} run --rm --no-deps ${service} python -m alembic current 2>&1")
      if echo "$alembic_out" | grep -q '(head)'; then
        ok "${service}: alembic current == head"
      else
        bad "${service}: alembic current != head (${alembic_out})"
      fi
    done
  else
    note "could not run 'docker compose ps' on ${HOST} (set SUDO_PASSWORD to enable container/alembic checks)"
  fi
else
  note "could not read ${REMOTE_DIR}/.compose-flags — has deploy-staging.sh run yet?"
fi

# ── Auth / Security-tab API checks (requires the seeded superadmin password) ─
if [[ -n "${SEYALRUN_ADMIN_PASSWORD:-}" ]]; then
  login_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"admin\",\"password\":\"${SEYALRUN_ADMIN_PASSWORD}\"}")
  TOKEN=$(echo "$login_resp" | json_get access_token)
  if [[ -n "$TOKEN" ]]; then
    ok "superadmin login returns a JWT"
  else
    bad "superadmin login failed: ${login_resp}"
  fi

  if [[ -n "$TOKEN" ]]; then
    AUTH=(-H "Authorization: Bearer ${TOKEN}")

    # Personal Access Token create / use / revoke
    pat_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/api-tokens" "${AUTH[@]}" \
      -H 'Content-Type: application/json' -d '{"name":"verify-staging","scopes":["read"]}')
    PAT_ID=$(echo "$pat_resp" | json_get id)
    PAT_TOKEN=$(echo "$pat_resp" | json_get token)
    [[ -n "$PAT_ID" && -n "$PAT_TOKEN" ]] \
      && ok "PAT created (id=${PAT_ID})" \
      || bad "PAT creation failed: ${pat_resp}"

    if [[ -n "$PAT_TOKEN" ]]; then
      pat_code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "${EDGE_HTTPS}/api/v1/users" -H "Authorization: Bearer ${PAT_TOKEN}")
      [[ "$pat_code" == "200" ]] && ok "PAT authenticates GET /users (200)" || bad "PAT auth check returned ${pat_code} (want 200)"
    fi

    if [[ -n "$PAT_ID" ]]; then
      revoke_code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/api-tokens/${PAT_ID}" "${AUTH[@]}")
      [[ "$revoke_code" == "204" ]] && ok "PAT revoked (204)" || bad "PAT revoke returned ${revoke_code} (want 204)"

      pat_code2=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "${EDGE_HTTPS}/api/v1/users" -H "Authorization: Bearer ${PAT_TOKEN}")
      [[ "$pat_code2" == "401" ]] && ok "revoked PAT rejected (401)" || bad "revoked PAT returned ${pat_code2} (want 401)"
    fi

    # Command Group + Command Filter CRUD
    cg_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/command-groups" "${AUTH[@]}" \
      -H 'Content-Type: application/json' \
      -d '{"name":"verify-staging-cg","description":"verify-staging","patterns":["^rm\\s"],"match_type":"regex"}')
    CG_ID=$(echo "$cg_resp" | json_get id)
    [[ -n "$CG_ID" ]] && ok "command group created (id=${CG_ID})" || bad "command group creation failed: ${cg_resp}"

    if [[ -n "$CG_ID" ]]; then
      cf_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/command-filters" "${AUTH[@]}" \
        -H 'Content-Type: application/json' \
        -d "{\"name\":\"verify-staging-cf\",\"command_group_id\":\"${CG_ID}\",\"action\":\"deny\",\"priority\":10,\"enabled\":true}")
      CF_ID=$(echo "$cf_resp" | json_get id)
      [[ -n "$CF_ID" ]] && ok "command filter created (id=${CF_ID})" || bad "command filter creation failed: ${cf_resp}"

      [[ -n "$CF_ID" ]] && curl -sk --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/command-filters/${CF_ID}" "${AUTH[@]}" >/dev/null
      curl -sk --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/command-groups/${CG_ID}" "${AUTH[@]}" >/dev/null
    fi

    # Login ACL CRUD — resolve the admin user id first
    ADMIN_USER_ID=$(curl -sk --max-time 10 "${EDGE_HTTPS}/api/v1/users" "${AUTH[@]}" | python3 -c "import sys,json; users=json.load(sys.stdin); print(next((u['id'] for u in users if u.get('username')=='admin'),''))" 2>/dev/null)
    la_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/login-acls" "${AUTH[@]}" \
      -H 'Content-Type: application/json' \
      -d "{\"name\":\"verify-staging-acl\",\"action\":\"allow\",\"user_id\":\"${ADMIN_USER_ID}\",\"days_of_week\":[0,1,2,3,4,5,6]}")
    LA_ID=$(echo "$la_resp" | json_get id)
    [[ -n "$LA_ID" ]] && ok "login ACL created (id=${LA_ID})" || bad "login ACL creation failed: ${la_resp}"
    [[ -n "$LA_ID" ]] && curl -sk --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/login-acls/${LA_ID}" "${AUTH[@]}" >/dev/null

    # Credential create -> response excludes plaintext -> ciphertext at rest -> delete
    cred_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/credentials" "${AUTH[@]}" \
      -H 'Content-Type: application/json' \
      -d '{"name":"verify-staging-cred","username":"verify","secret_type":"password","secret":{"password":"VerifyStaging123!"},"credential_scope":"template"}')
    CRED_ID=$(echo "$cred_resp" | json_get id)
    if [[ -n "$CRED_ID" ]]; then
      ok "credential created (id=${CRED_ID})"
      echo "$cred_resp" | grep -q "VerifyStaging123" \
        && bad "credential API response leaks plaintext secret" \
        || ok "credential API response does not leak plaintext secret"

      if [[ -n "$COMPOSE_FLAGS" ]]; then
        db_row=$(remote_sudo_out "cd ${REMOTE_DIR} && docker compose ${COMPOSE_FLAGS} exec -T postgres psql -U \$(grep '^DB_USER=' .env | cut -d= -f2) -d \$(grep '^INVENTORY_DB_NAME=' .env | cut -d= -f2) -tAc \"SELECT secret_ciphertext FROM za_credentials WHERE id='${CRED_ID}'\"")
        if [[ -n "$db_row" ]]; then
          echo "$db_row" | grep -q "VerifyStaging123" \
            && bad "za_credentials.secret_ciphertext stores plaintext" \
            || ok "za_credentials.secret_ciphertext is not plaintext (encrypted at rest)"
        else
          note "could not read za_credentials row directly (bare-metal DB or sudo unavailable) — response-leak check above stands"
        fi
      fi

      curl -sk --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/credentials/${CRED_ID}" "${AUTH[@]}" >/dev/null
    else
      bad "credential creation failed: ${cred_resp}"
    fi

    # ── Phase 2: ssh session REST smoke test ─────────────────────────────────
    VERIFY_HOST_ID="${VERIFY_SSH_HOST_ID:-}"
    if [[ -n "$VERIFY_HOST_ID" ]]; then
      sess_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/ssh/sessions" "${AUTH[@]}" \
        -H 'Content-Type: application/json' -d "{\"host_id\":\"${VERIFY_HOST_ID}\"}")
      SESSION_ID=$(echo "$sess_resp" | json_get session_id)
      if [[ -n "$SESSION_ID" ]]; then
        ok "ssh session created (id=${SESSION_ID})"
        term_code=$(curl -sk -o/dev/null -w '%{http_code}' --max-time 10 \
          -X DELETE "${EDGE_HTTPS}/api/v1/ssh/sessions/${SESSION_ID}" "${AUTH[@]}")
        [[ "$term_code" == "204" ]] \
          && ok "ssh session terminated (204)" \
          || bad "ssh session terminate returned ${term_code}"
      else
        note "ssh session creation returned no session_id — host may not be reachable (set VERIFY_SSH_HOST_ID to a known-good host)"
      fi
    else
      note "VERIFY_SSH_HOST_ID not set — skipping Phase 2 ssh session smoke test"
    fi

    # ── Phase 2: recordings list ──────────────────────────────────────────────
    rec_code=$(curl -sk -o/dev/null -w '%{http_code}' --max-time 10 "${EDGE_HTTPS}/api/v1/recordings" "${AUTH[@]}")
    [[ "$rec_code" == "200" ]] \
      && ok "GET /recordings returns 200" \
      || bad "GET /recordings returned ${rec_code} (want 200)"

    # ── Phase 3: job-run smoke test (bash_script, run_local=true) ─────────────
    proj_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/projects" "${AUTH[@]}" \
      -H 'Content-Type: application/json' \
      -d '{"name":"verify-staging-project","description":"","source_type":"local"}' 2>/dev/null)
    PROJ_ID=$(echo "$proj_resp" | json_get id)
    if [[ -n "$PROJ_ID" ]]; then
      tmpl_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/job-templates" "${AUTH[@]}" \
        -H 'Content-Type: application/json' \
        -d "{\"project_id\":\"${PROJ_ID}\",\"name\":\"verify-template\",\"action_type\":\"bash_script\",
             \"script_content\":\"echo verify-ok\",\"target_scope\":\"hosts\",\"target_host_ids\":[],
             \"default_params\":{\"run_local\":true}}" 2>/dev/null)
      TMPL_ID=$(echo "$tmpl_resp" | json_get id)
      if [[ -n "$TMPL_ID" ]]; then
        run_resp=$(curl -sk --max-time 10 -X POST "${EDGE_HTTPS}/api/v1/job-templates/${TMPL_ID}/run" \
          "${AUTH[@]}" -H 'Content-Type: application/json' -d '{}' 2>/dev/null)
        RUN_ID=$(echo "$run_resp" | json_get run_id)
        if [[ -n "$RUN_ID" ]]; then
          for i in $(seq 1 15); do
            run_status=$(curl -sk --max-time 10 "${EDGE_HTTPS}/api/v1/job-runs/${RUN_ID}" "${AUTH[@]}" | json_get status 2>/dev/null)
            [[ "$run_status" =~ ^(success|failed|error|cancelled)$ ]] && break; sleep 1
          done
          [[ "$run_status" == "success" ]] \
            && ok "Phase 3 job run completed (status=success)" \
            || bad "Phase 3 job run: expected status=success, got '${run_status:-timeout}'"
          curl -sk --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/job-runs/${RUN_ID}" "${AUTH[@]}" >/dev/null 2>&1 || true
        else
          bad "Phase 3 job run: no run_id in response (${run_resp})"
        fi
        curl -sk --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/job-templates/${TMPL_ID}" "${AUTH[@]}" >/dev/null 2>&1 || true
      else
        bad "Phase 3 job template creation failed: ${tmpl_resp}"
      fi
      curl -sk --max-time 10 -X DELETE "${EDGE_HTTPS}/api/v1/projects/${PROJ_ID}" "${AUTH[@]}" >/dev/null 2>&1 || true
    else
      bad "Phase 3 project creation failed: ${proj_resp}"
    fi

    # ── Phase 3: Zabbix webhook HMAC check ───────────────────────────────────
    WEBHOOK_SECRET=$(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" \
      "grep '^ZABBIX_WEBHOOK_HMAC_SECRET=' ${REMOTE_DIR}/.env | cut -d= -f2" 2>/dev/null)
    if [[ -n "$WEBHOOK_SECRET" ]]; then
      WH_PAYLOAD='{"triggerid":"0","eventid":"verify-0","hostname":"none","severity":0,"value":0}'
      WH_SIG=$(printf '%s' "$WH_PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')
      wh_ok=$(curl -sk -o/dev/null -w '%{http_code}' --max-time 10 \
        -X POST "${EDGE_HTTPS}/webhook/zabbix" \
        -H 'Content-Type: application/json' \
        -H "X-SeyalRun-Signature: ${WH_SIG}" -d "$WH_PAYLOAD")
      [[ "$wh_ok" == "202" ]] \
        && ok "Phase 3 webhook valid-HMAC accepted (202)" \
        || bad "Phase 3 webhook valid-HMAC returned ${wh_ok} (want 202)"
      wh_bad=$(curl -sk -o/dev/null -w '%{http_code}' --max-time 10 \
        -X POST "${EDGE_HTTPS}/webhook/zabbix" \
        -H 'Content-Type: application/json' \
        -H "X-SeyalRun-Signature: deadbeef" -d "$WH_PAYLOAD")
      [[ "$wh_bad" == "401" ]] \
        && ok "Phase 3 webhook invalid-HMAC rejected (401)" \
        || bad "Phase 3 webhook invalid-HMAC returned ${wh_bad} (want 401)"
    else
      note "ZABBIX_WEBHOOK_HMAC_SECRET not readable — skipping webhook HMAC checks"
    fi

    # ── Phase 3: metrics dashboard ────────────────────────────────────────────
    metrics_resp=$(curl -sk --max-time 10 "${EDGE_HTTPS}/api/v1/metrics/dashboard" "${AUTH[@]}" 2>/dev/null)
    echo "$metrics_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'hosts' in d" 2>/dev/null \
      && ok "Phase 3 GET /metrics/dashboard returns aggregate data" \
      || bad "Phase 3 metrics dashboard failed: ${metrics_resp}"
  fi
else
  note "SEYALRUN_ADMIN_PASSWORD not set — skipping login/PAT/Security-tab CRUD checks"
fi

echo ""
echo "  ${pass}/${total} checks passed"
[[ "$pass" -eq "$total" ]]
