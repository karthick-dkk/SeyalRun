#!/usr/bin/env bash
# SeyalRun v2.0 — backend admin password reset (no UI/API needed).
#
# Sets a new password for the admin user directly in the identity DB using
# the service's own Argon2id hasher, and clears any login lockout for the
# account. Run from the repo root on the host where the stack runs
# (locally, or /opt/seyalrun-v2 on staging — sudo is auto-detected).
#
# Usage:
#   bash ops/reset-admin-password.sh                     # prompts (hidden) for the new password
#   bash ops/reset-admin-password.sh -u Admin            # explicit username (default: Admin)
#   bash ops/reset-admin-password.sh --no-force-change   # don't require rotation at next login
#   bash ops/reset-admin-password.sh --factory           # delete user + reseed -> Admin/seyalrun (forced change)
#   SEYALRUN_NEW_PASSWORD='...' bash ops/reset-admin-password.sh   # non-interactive
#
# Notes:
#   - By default the account is flagged must_change_password, so the reset
#     value is only a handover credential — the user picks their own at login.
#   - Existing JWTs stay valid until expiry (JWT_EXPIRE_MINUTES, default 8h);
#     a reset does not kill live sessions.

set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[->]${NC} $*"; }
fail() { echo -e "${RED}[X]${NC} $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[[ -f .env ]] || fail ".env not found in $REPO_ROOT — is the stack deployed here?"

USERNAME="Admin"
FORCE_CHANGE="true"
FACTORY="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--user)          USERNAME="$2"; shift 2 ;;
    --no-force-change)  FORCE_CHANGE="false"; shift ;;
    --factory)          FACTORY="true"; shift ;;
    -h|--help)          grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                  fail "unknown argument: $1 (see --help)" ;;
  esac
done

# ── docker / compose plumbing (sudo fallback for staging hosts) ──────────────
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  sudo -n docker info >/dev/null 2>&1 || fail "docker unreachable (daemon down, or sudo needs a password — run: sudo -v first)"
  DOCKER=(sudo docker)
fi

# Reuse the deploy's compose flags when present (staging); default to the
# local dev overlay otherwise.
COMPOSE=("${DOCKER[@]}" compose)
if [[ -f .compose-flags ]]; then
  while IFS= read -r flag; do
    # shellcheck disable=SC2206 # flags are two-word entries ("-f file" / "--profile name")
    COMPOSE+=($flag)
  done < .compose-flags
else
  COMPOSE+=(-f docker-compose.yml -f docker-compose.db.yml --profile postgres-db)
fi

env_get() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | xargs; }
DB_USER="$(env_get DB_USER)";        DB_USER="${DB_USER:-seyalrun}"
DB_NAME="$(env_get IDENTITY_DB_NAME)"; DB_NAME="${DB_NAME:-seyalrun_identity}"

psql_id() { "${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" "$@"; }

# ── factory mode: delete + reseed -> Admin/seyalrun, forced change ───────────
if [[ "$FACTORY" == "true" ]]; then
  info "factory reset: deleting '${USERNAME}' and reseeding..."
  # SQL via stdin: psql only interpolates -v variables in stdin/-f input, not -c.
  psql_id -v uname="$USERNAME" >/dev/null <<'SQL'
DELETE FROM za_user_roles WHERE user_id IN (SELECT id FROM za_users WHERE username = :'uname');
DELETE FROM za_users WHERE username = :'uname';
DELETE FROM za_login_attempts WHERE key LIKE lower(:'uname') || '|%';
SQL
  "${COMPOSE[@]}" run --rm --no-deps identity-service python -m app.seed
  ok "factory reset done — log in with the default password; a change is forced immediately"
  exit 0
fi

# ── read + validate the new password (mirrors app/_pwpolicy.py) ──────────────
NEW_PASSWORD="${SEYALRUN_NEW_PASSWORD:-}"
if [[ -z "$NEW_PASSWORD" ]]; then
  read -r -s -p "New password for '${USERNAME}': " NEW_PASSWORD; echo ""
  read -r -s -p "Confirm: " CONFIRM; echo ""
  [[ "$NEW_PASSWORD" == "$CONFIRM" ]] || fail "passwords do not match"
fi
[[ ${#NEW_PASSWORD} -ge 8 ]] || fail "password must be at least 8 characters"
LOWERED="$(printf '%s' "$NEW_PASSWORD" | tr '[:upper:]' '[:lower:]')"
[[ "$LOWERED" != "seyalrun" ]] || fail "refusing the shipped default password"
[[ "$LOWERED" != "$(printf '%s' "$USERNAME" | tr '[:upper:]' '[:lower:]')" ]] || fail "password must not be the username"

# ── hash inside the identity image (same Argon2id params as the app) ─────────
# Password travels via stdin, never argv (argv is visible in `ps`).
info "hashing password with the service's own hasher..."
HASH=$(printf '%s' "$NEW_PASSWORD" | "${COMPOSE[@]}" run --rm --no-deps -T identity-service \
  python -c "import sys; from app.security import hash_password; print(hash_password(sys.stdin.read()))" | tail -1)
[[ "$HASH" == \$argon2* ]] || fail "unexpected hasher output: ${HASH:0:40}"

# ── apply + clear lockout ─────────────────────────────────────────────────────
# SQL via stdin: psql only interpolates -v variables in stdin/-f input, not -c.
RESULT=$(psql_id -v uname="$USERNAME" -v hash="$HASH" -v fc="$FORCE_CHANGE" -tA <<'SQL'
UPDATE za_users SET password_hash = :'hash', must_change_password = :'fc'::boolean
 WHERE username = :'uname' RETURNING username;
DELETE FROM za_login_attempts WHERE key LIKE lower(:'uname') || '|%';
SQL
)
[[ -n "$RESULT" ]] || fail "user '${USERNAME}' not found in ${DB_NAME}.za_users"

ok "password updated for '${USERNAME}' (login lockout cleared)"
if [[ "$FORCE_CHANGE" == "true" ]]; then
  ok "must_change_password is SET — the next login forces a rotation"
else
  ok "must_change_password is cleared — the new password is final"
fi