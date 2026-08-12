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
SSH_USER="${SSH_USER:-test}"
REMOTE_DIR="/opt/seyalrun-v2"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[->]${NC} $*"; }
fail() { echo -e "${RED}[X]${NC} $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

info "Syncing compose files and ops/ to ${HOST}..."
rsync -az \
  --include='docker-compose*.yml' --include='ops/' --include='ops/**' \
  --exclude='*' \
  "${REPO_ROOT}/" "${SSH_USER}@${HOST}:${REMOTE_DIR}/"

ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "cd ${REMOTE_DIR} && VERSION_OVERRIDE='${VERSION}' bash -s" <<'REMOTE'
set -euo pipefail
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

echo "[*] image provenance actually running:"
docker compose "${FLAGS[@]}" ps --format "{{.Service}}\t{{.Image}}" | sort
REMOTE

ok "staging is running from published images"
info "Verifying..."
SUDO_PASSWORD="${SUDO_PASSWORD:-}" SSH_USER="$SSH_USER" bash "${REPO_ROOT}/ops/verify-staging.sh" "$HOST"
