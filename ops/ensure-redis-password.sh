#!/bin/bash
# Gives an existing .env a Redis password (architecture review F-4). Idempotent.
#
#   ops/ensure-redis-password.sh [path/to/.env]     (default: ./.env)
#
# Redis now starts with --requirepass and compose refuses to start without
# REDIS_PASSWORD, so an install upgraded in place needs one added. This:
#   * generates REDIS_PASSWORD if it is missing or empty, and
#   * points REDIS_URL at it when REDIS_URL is the bundled, unauthenticated
#     default (redis://redis:6379/0). A custom REDIS_URL (external Redis) is
#     left alone, with a warning, because only the operator knows its password.
#
# install.sh carries the same logic inline (it runs standalone via curl | bash);
# tests/test_redis_auth.py keeps the two in step.
set -euo pipefail

ENV_FILE="${1:-.env}"
[[ -f "$ENV_FILE" ]] || { echo "[!] $ENV_FILE not found — nothing to do" >&2; exit 0; }

_set_env() {  # _set_env KEY VALUE — replace the line, or append it
  if grep -q "^$1=" "$ENV_FILE"; then
    sed -i.bak "s|^$1=.*|$1=$2|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
  fi
}

if ! grep -qE '^REDIS_PASSWORD=.+' "$ENV_FILE"; then
  _set_env REDIS_PASSWORD "$(openssl rand -hex 32)"
  echo "[OK] generated REDIS_PASSWORD in $ENV_FILE"
fi
REDIS_PW="$(grep -E '^REDIS_PASSWORD=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"

CUR_URL="$(grep -E '^REDIS_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//')"
case "$CUR_URL" in
  ""|"redis://redis:6379/0"|"redis://:"*"@redis:6379/0")
    if [[ "$CUR_URL" != "redis://:${REDIS_PW}@redis:6379/0" ]]; then
      _set_env REDIS_URL "redis://:${REDIS_PW}@redis:6379/0"
      echo "[OK] REDIS_URL now authenticates to the bundled Redis"
    fi
    ;;
  *)
    echo "[!] REDIS_URL is custom ($(echo "$CUR_URL" | sed 's|//[^@]*@|//***@|')) — left unchanged; make sure it carries that Redis's password" >&2
    ;;
esac
