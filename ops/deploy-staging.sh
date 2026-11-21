#!/bin/bash
# Deploys SeyalRun to a staging host from source: rsyncs the repo to
# $REMOTE_DIR (default /opt/seyalrun-v2), runs ops/_staging-bootstrap.sh there (generates .env +
# a self-signed TLS cert if missing, brings up the stack with Docker
# Compose — falling back to docker-compose.db.yml's Dockerized Postgres if
# no bare-metal Postgres is reachable, runs Alembic migrations + the
# superadmin seed), then runs ops/verify-staging.sh.
#
# Usage:
#   ops/deploy-staging.sh <HOST>
#
# Environment: SSH_USER, REMOTE_DIR, SUDO_PASSWORD — see ops/_staging-lib.sh.
# STAGING_BASE_VERSION=x.y.z reuses that release's published images for Python
# deps when the host's PyPI link is too slow (refused if deps changed since).
# sudo is only used when the SSH user is not in the docker group.
#
# Idempotent / safe to re-run: reuses the existing remote .env/TLS cert and
# `docker compose up -d` only (re)creates changed containers.

set -euo pipefail

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  echo "Usage: $0 <HOST>  — e.g. $0 192.168.64.2" >&2
  exit 1
fi
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=ops/_staging-lib.sh
source "${REPO_ROOT}/ops/_staging-lib.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[->]${NC} $*"; }
fail() { echo -e "${RED}[X]${NC} $*"; exit 1; }

echo "=== SeyalRun v2.0 deploy -> ${SSH_USER}@${HOST}:${REMOTE_DIR} ==="

info "[1/5] Ensuring ${REMOTE_DIR} exists and is owned by ${SSH_USER}..."
ensure_remote_dir
log "${REMOTE_DIR} ready"

info "[2/5] Syncing repo to ${REMOTE_DIR} (test-owned — no sudo needed)..."
rsync -az --delete \
  --exclude='.git' --exclude='node_modules' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='dist' --exclude='.env' --exclude='tls' --exclude='seed-output.txt' \
  --exclude='.compose-flags' --exclude='backups' \
  "${REPO_ROOT}/" "${SSH_USER}@${HOST}:${REMOTE_DIR}/"
log "repo synced"

# STAGING_BASE_VERSION=x.y.z: reuse that release's published images for the
# Python dependencies (see _staging-bootstrap.sh). Only valid while no
# requirements file or Dockerfile has changed since the tag — checked here,
# where the git history is.
if [[ -n "${STAGING_BASE_VERSION:-}" ]]; then
  git -C "$REPO_ROOT" diff --quiet "v${STAGING_BASE_VERSION}" -- '*requirements*.txt' 'core/services/*/Dockerfile' \
    || fail "requirements or Dockerfiles changed since v${STAGING_BASE_VERSION} — do a full build (unset STAGING_BASE_VERSION)"
  info "Python deps reused from published v${STAGING_BASE_VERSION} images (unchanged since that tag)"
fi

info "[3/5] Running bootstrap (build + up -d + migrations + seed) — this can take several minutes..."
remote_docker "cd '${REMOTE_DIR}' && EDGE_HOST='${HOST}' STAGING_BASE_VERSION='${STAGING_BASE_VERSION:-}' bash ops/_staging-bootstrap.sh"
log "bootstrap complete"

info "[4/5] Superadmin seed output (shown once, then removed from the host):"
SEED_OUTPUT=$(rssh "cat '${REMOTE_DIR}/seed-output.txt' 2>/dev/null && rm -f '${REMOTE_DIR}/seed-output.txt'" || true)
echo "$SEED_OUTPUT"
SEED_PASSWORD=$(echo "$SEED_OUTPUT" | sed -n 's/.*will not be shown again): //p')

info "[5/5] Running verification..."
SEYALRUN_ADMIN_PASSWORD="${SEYALRUN_ADMIN_PASSWORD:-$SEED_PASSWORD}" bash "${REPO_ROOT}/ops/verify-staging.sh" "${HOST}"
