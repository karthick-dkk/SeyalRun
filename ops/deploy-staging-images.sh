#!/bin/bash
# Redeploy staging from PUBLISHED Docker images instead of a source build.
#
#   ops/deploy-staging-images.sh <HOST> [VERSION]
#
# This is step 7 of the release loop and the one that is usually skipped. A
# source build exercises docker-compose.yml; a release runs
# docker-compose.prod.yml, and the two drifted badly enough that prod's
# edge-proxy — the only host-published service — could never start:
# INTERNAL_PROTO and INTERNAL_PROXY_SSL_VERIFY were missing, nginx refused the
# rendered config, and nothing was reachable. Source builds stayed green
# throughout. Only pulling the real images can catch that class of bug.
#
# Reuses the host's existing .env, TLS material and database volumes; the only
# change is where the service images come from.
set -euo pipefail

HOST="${1:-}"
VERSION="${2:-}"
[[ -n "$HOST" ]] || { echo "Usage: $0 <HOST> [VERSION]" >&2; exit 1; }

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[->]${NC} $*"; }
fail() { echo -e "${RED}[X]${NC} $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=ops/_staging-lib.sh
source "${REPO_ROOT}/ops/_staging-lib.sh"   # SSH_USER, REMOTE_DIR, SUDO_PASSWORD

info "Syncing compose files and ops/ to ${HOST}..."
rsync -az \
  --include='docker-compose*.yml' --include='ops/' --include='ops/**' \
  --exclude='*' \
  "${REPO_ROOT}/" "${SSH_USER}@${HOST}:${REMOTE_DIR}/"

_docker_direct || fail "${SSH_USER} cannot run docker on ${HOST} without sudo — add it to the docker group"
rssh "cd ${REMOTE_DIR} && VERSION_OVERRIDE='${VERSION}' bash -s" <<'REMOTE'
set -euo pipefail
bash ops/ensure-redis-password.sh .env   # F-4: an older .env has no Redis password yet
set -a; source .env; set +a
export INTERNAL_TLS_DIR="${INTERNAL_TLS_DIR:-$(pwd)/tls/internal}"
[[ -n "${VERSION_OVERRIDE:-}" ]] && export SEYALRUN_VERSION="$VERSION_OVERRIDE"
echo "[*] deploying image tag: ${SEYALRUN_VERSION:-latest}"

# The db overlay supplies postgres, which prod compose does not declare (a real
# deployment points DB_HOST at a managed database). internal-tls carries only
# env + volume overrides, so it layers onto images the same as onto builds.
FLAGS=(-f docker-compose.prod.yml -f docker-compose.db.yml)
[[ -f tls/internal/ca.pem ]] && FLAGS+=(-f docker-compose.internal-tls.yml)
FLAGS+=(--profile postgres-db)
# verify-staging.sh reads these, so its container and Alembic checks inspect the
# published images rather than whatever the last source build left behind.
printf '%s\n' "${FLAGS[@]}" > .compose-flags

echo "[*] pulling published images..."
docker compose "${FLAGS[@]}" pull --quiet

echo "[*] starting from images..."
docker compose "${FLAGS[@]}" up -d --remove-orphans

echo "[*] waiting for health..."
for _ in $(seq 1 45); do
  unhealthy=$(docker compose "${FLAGS[@]}" ps --format "{{.Service}} {{.Health}}" | awk '$2!="" && $2!="healthy" {print $1}')
  [[ -z "$unhealthy" ]] && break
  sleep 3
done
[[ -z "${unhealthy:-}" ]] || { echo "[X] still unhealthy: $unhealthy"; docker compose "${FLAGS[@]}" logs --tail=40 $unhealthy; exit 1; }

# Persist the tag only once the stack is healthy on it: every later compose
# call (migrations below, verify-staging.sh, a manual `up`) reads .env, and a
# stale pin there makes them run the previous release's images.
if [[ -n "${VERSION_OVERRIDE:-}" ]]; then
  if grep -q '^SEYALRUN_VERSION=' .env; then
    sed -i "s/^SEYALRUN_VERSION=.*/SEYALRUN_VERSION=${VERSION_OVERRIDE}/" .env
  else
    printf 'SEYALRUN_VERSION=%s\n' "$VERSION_OVERRIDE" >> .env
  fi
  echo "[*] .env pinned to SEYALRUN_VERSION=${VERSION_OVERRIDE}"
fi

# A release can add a service (posture-service in 3.0) or a migration; the
# images carry them but nothing applied them on this path. alembic upgrade head
# is idempotent, and /health only checks DB reachability, so running it after
# the stack is up is safe. </dev/null: `compose run` must not eat this heredoc.
echo "[*] applying database migrations from the images..."
for svc in identity-service inventory-service terminal-service recording-service \
           automation-service zabbix-integration-service metrics-service posture-service; do
  docker compose "${FLAGS[@]}" run --rm --no-deps -T "$svc" python -m alembic upgrade head </dev/null 2>&1 | tail -1
done

echo "[*] image provenance actually running:"
docker compose "${FLAGS[@]}" ps --format "{{.Service}}\t{{.Image}}" | sort
REMOTE

ok "staging is running from published images"
info "Verifying..."
SUDO_PASSWORD="${SUDO_PASSWORD:-}" SSH_USER="$SSH_USER" REMOTE_DIR="$REMOTE_DIR" \
  SEYALRUN_ADMIN_PASSWORD="${SEYALRUN_ADMIN_PASSWORD:-}" bash "${REPO_ROOT}/ops/verify-staging.sh" "$HOST"
