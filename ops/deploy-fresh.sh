#!/bin/bash
# SeyalRun v2.0 — Fresh deploy script.
# Run ON the staging host as the SSH user (not root).
# Requires sudo access. SUDO_PASSWORD must be set in the environment.
#
# Usage (from your local Mac):
#   SUDO_PASSWORD=test SSH_USER=test bash ops/deploy-fresh.sh 192.168.64.7
#
# Usage (directly on the host after SCP):
#   SUDO_PASSWORD=test EDGE_HOST=192.168.64.7 bash /tmp/deploy-fresh.sh

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
REMOTE_DIR="/opt/seyalrun-v2"
ARCHIVE="/tmp/seyalrun-v2.tar.gz"
# NOTE: EDGE_HOST and DOCKER_BIN are resolved later — must not run on macOS

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[->]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[X]${NC} $*"; exit 1; }

# ── Detect if running locally (SSH mode) or directly on the host ──────────────
if [[ "${1:-}" =~ ^[0-9] ]]; then
  HOST="$1"
  SSH_USER="${SSH_USER:-test}"
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

  echo "=== SeyalRun v2.0 fresh deploy -> ${SSH_USER}@${HOST}:${REMOTE_DIR} ==="

  info "[1/4] Packaging repo..."
  # COPYFILE_DISABLE suppresses macOS ._* resource fork files in the archive
  COPYFILE_DISABLE=1 tar \
      --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
      --exclude='.env'  --exclude='tls'          --exclude='dist' \
      --exclude='backups' --exclude='seed-output.txt' --exclude='.compose-flags' \
      -czf /tmp/seyalrun-v2.tar.gz -C "$(dirname "$REPO_ROOT")" "$(basename "$REPO_ROOT")"
  ok "archive: /tmp/seyalrun-v2.tar.gz"

  info "[2/4] Copying archive to ${HOST}..."
  scp -o StrictHostKeyChecking=accept-new /tmp/seyalrun-v2.tar.gz "${SSH_USER}@${HOST}:/tmp/"
  ok "archive uploaded"

  info "[3/4] Running deploy script on ${HOST}..."
  scp -o StrictHostKeyChecking=accept-new "${BASH_SOURCE[0]}" "${SSH_USER}@${HOST}:/tmp/deploy-fresh.sh"
  ssh -o StrictHostKeyChecking=accept-new "${SSH_USER}@${HOST}" \
    "SUDO_PASSWORD='${SUDO_PASSWORD}' EDGE_HOST='${HOST}' bash /tmp/deploy-fresh.sh"

  info "[4/4] Cleaning up local archive..."
  rm -f /tmp/seyalrun-v2.tar.gz
  ok "Done."
  exit 0
fi

# ── Running ON the host (Linux) ───────────────────────────────────────────────
: "${SUDO_PASSWORD:?SUDO_PASSWORD must be set}"

# Resolve EDGE_HOST (Linux-only hostname -I is safe here — we're on the remote host)
EDGE_HOST="${EDGE_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
EDGE_HOST="${EDGE_HOST:-$(hostname -f 2>/dev/null)}"

# Find docker binary — sudo has a restricted PATH on many systems
DOCKER_BIN="$(command -v docker 2>/dev/null || true)"
[[ -x "$DOCKER_BIN" ]] || DOCKER_BIN="$(sudo -n which docker 2>/dev/null || true)"
[[ -x "$DOCKER_BIN" ]] || DOCKER_BIN="/usr/bin/docker"
[[ -x "$DOCKER_BIN" ]] || fail "docker not found — is Docker installed?"

# Wrapper: runs docker compose with sudo, preserving PATH so docker is found.
# DOCKER_BUILDKIT=0 uses the legacy builder which inherits host DNS — avoids
# BuildKit DNS resolution failures on servers with custom DNS setups.
dc() {
  echo "$SUDO_PASSWORD" | sudo -S env PATH="$PATH" DOCKER_BUILDKIT=0 "$DOCKER_BIN" compose "$@"
}

echo ""
echo "======================================================"
echo "  SeyalRun v2.0 — Fresh Deploy"
echo "  Host: $(hostname) | $(date)"
echo "  Docker: $DOCKER_BIN"
echo "======================================================"

# ── Step 1: Extract archive ───────────────────────────────────────────────────
info "[1/8] Extracting archive to ${REMOTE_DIR}..."
# Ensure the directory and all existing files are writable by this user.
# Without -R, files written by root during prior Docker runs stay root-owned
# and tar cannot overwrite them, silently leaving old versions in place.
echo "$SUDO_PASSWORD" | sudo -S bash -c "mkdir -p '${REMOTE_DIR}' && chown -R $(whoami):$(whoami) '${REMOTE_DIR}'" 2>/dev/null
# --warning=no-unknown-keyword suppresses macOS Apple xattr header warnings
tar --warning=no-unknown-keyword -xzf "${ARCHIVE}" --strip-components=1 -C "${REMOTE_DIR}" 2>/dev/null || \
  tar -xzf "${ARCHIVE}" --strip-components=1 -C "${REMOTE_DIR}" 2>&1 | grep -v 'LIBARCHIVE.xattr' || true
# Remove macOS resource fork files (._*) — left by old macOS tar extractions.
# These match *.py globs and cause "null bytes" SyntaxErrors at runtime.
find "${REMOTE_DIR}" -name '._*' -delete 2>/dev/null || true
ok "repo extracted to ${REMOTE_DIR}"
cd "${REMOTE_DIR}"

# ── Step 2: Generate .env ─────────────────────────────────────────────────────
info "[2/8] Generating .env..."
if [[ -f .env ]]; then
  warn ".env already exists — reusing (delete it for a truly fresh deploy)"
else
  cp .env.example .env
  rand_hex() { openssl rand -hex 32; }
  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(rand_hex)|" .env
  sed -i "s|^SERVICE_JWT_SECRET=.*|SERVICE_JWT_SECRET=$(rand_hex)|" .env
  sed -i "s|^API_TOKEN_PEPPER=.*|API_TOKEN_PEPPER=$(rand_hex)|" .env
  sed -i "s|^ZA_VAULT_PASSWORD=.*|ZA_VAULT_PASSWORD=$(rand_hex)|" .env
  sed -i "s|^ZA_VAULT_SALT=.*|ZA_VAULT_SALT=$(openssl rand -hex 16)|" .env
  sed -i "s|^ZABBIX_WEBHOOK_HMAC_SECRET=.*|ZABBIX_WEBHOOK_HMAC_SECRET=$(rand_hex)|" .env
  sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$(openssl rand -hex 16)|" .env
  ok ".env generated with random secrets"
fi
# Redis auth (F-4): fresh or reused .env alike — compose won't start Redis without it.
bash ops/ensure-redis-password.sh .env

# ── Step 3: TLS cert ──────────────────────────────────────────────────────────
info "[3/8] Setting up TLS certificate..."
mkdir -p tls
if [[ ! -f tls/cert.pem || ! -f tls/key.pem ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout tls/key.pem -out tls/cert.pem \
    -subj "/CN=${EDGE_HOST}" >/dev/null 2>&1
  ok "self-signed TLS cert generated for ${EDGE_HOST}"
else
  warn "TLS cert already exists — reusing"
fi
chmod 644 tls/key.pem tls/cert.pem
sed -i "s|^TLS_CERT_PATH=.*|TLS_CERT_PATH=$(pwd)/tls/cert.pem|" .env
sed -i "s|^TLS_KEY_PATH=.*|TLS_KEY_PATH=$(pwd)/tls/key.pem|" .env

# ── Step 4: DB + CORS config ──────────────────────────────────────────────────
info "[4/8] Configuring DB and CORS..."
EDGE_HTTPS_PORT="$(grep '^EDGE_HTTPS_PORT=' .env | cut -d= -f2)"
EDGE_HTTPS_PORT="${EDGE_HTTPS_PORT:-8443}"
sed -i "s|^FRONTEND_ORIGIN=.*|FRONTEND_ORIGIN=https://${EDGE_HOST}:${EDGE_HTTPS_PORT}|" .env

COMPOSE_FILES=(-f docker-compose.yml)
COMPOSE_PROFILE=()

# If .compose-flags exists AND already has the db overlay, reuse it — prior deploy was correct.
# If it exists but lacks docker-compose.db.yml it's from an old broken deploy; re-detect.
if [[ -f .compose-flags ]] && grep -q 'docker-compose.db.yml' .compose-flags; then
  mapfile -t _flags < .compose-flags
  for flag in "${_flags[@]}"; do
    case "$flag" in
      "-f "*) COMPOSE_FILES+=("$flag") ;;
      "--profile "*) COMPOSE_PROFILE+=("$flag") ;;
    esac
  done
  DB_HOST_VAL="$(grep '^DB_HOST=' .env | cut -d= -f2)"
  warn "reusing existing DB config: DB_HOST=${DB_HOST_VAL}"
else
  sed -i "s|^DB_ENGINE=.*|DB_ENGINE=postgres|" .env
  # Always use Dockerized Postgres: containers cannot reach 127.0.0.1 even when
  # bare-metal Postgres is on the host (that address resolves to container loopback).
  sed -i "s|^DB_HOST=.*|DB_HOST=postgres|" .env
  sed -i "s|^DB_SSLMODE=.*|DB_SSLMODE=disable|" .env
  COMPOSE_FILES+=(-f docker-compose.db.yml)
  COMPOSE_PROFILE=(--profile postgres-db)
  if timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/5432" 2>/dev/null; then
    warn "bare-metal Postgres detected at 127.0.0.1:5432 — using Dockerized Postgres overlay (containers can't reach host 127.0.0.1)"
  else
    warn "no bare-metal Postgres — using Dockerized Postgres"
  fi
  printf '%s\n' "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" > .compose-flags
fi

# Auto-detect HTTP port conflict and bump to 8090 if 8080 is occupied
CURRENT_HTTP_PORT="$(grep '^EDGE_HTTP_PORT=' .env | cut -d= -f2)"
CURRENT_HTTP_PORT="${CURRENT_HTTP_PORT:-8080}"
if timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/${CURRENT_HTTP_PORT}" 2>/dev/null; then
  NEW_HTTP_PORT=8090
  sed -i "s|^EDGE_HTTP_PORT=.*|EDGE_HTTP_PORT=${NEW_HTTP_PORT}|" .env
  warn "port ${CURRENT_HTTP_PORT} in use — EDGE_HTTP_PORT changed to ${NEW_HTTP_PORT}"
fi
ok "DB and CORS configured"

# ── Step 5: Build images ──────────────────────────────────────────────────────
info "[5/8] Building Docker images (this takes a few minutes)..."
BUILD_SERVICES=$(awk '/^  [a-zA-Z0-9_-]+:/{svc=$1} /^    build:/{print svc}' docker-compose.yml | tr -d ':')
for svc in $BUILD_SERVICES; do
  for attempt in 1 2 3; do
    echo "    building ${svc} (attempt ${attempt}/3)..."
    if dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" build "${svc}" 2>&1 | tail -3; then
      break
    elif [[ "$attempt" == 3 ]]; then
      fail "failed to build ${svc} after 3 attempts"
    fi
    sleep 3
  done
done
ok "all images built"

# ── Step 6: Start services ────────────────────────────────────────────────────
info "[6/8] Starting services..."
dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" up -d 2>&1 | tail -5
ok "services started"

# Wait for Dockerized Postgres and create databases if using overlay
if [[ "${COMPOSE_PROFILE[*]:-}" == *postgres-db* ]]; then
  info "Waiting for Postgres to be healthy..."
  for i in $(seq 1 30); do
    status=$(dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" ps postgres --format '{{.Health}}' 2>/dev/null || true)
    [[ "$status" == "healthy" ]] && break
    sleep 2
  done

  DB_USER="$(grep '^DB_USER=' .env | cut -d= -f2)"

  for dbname in \
    "$(grep '^IDENTITY_DB_NAME='   .env | cut -d= -f2)" \
    "$(grep '^INVENTORY_DB_NAME='  .env | cut -d= -f2)" \
    "$(grep '^TERMINAL_DB_NAME='   .env | cut -d= -f2 || echo seyalrun_terminal)" \
    "$(grep '^AUTOMATION_DB_NAME=' .env | cut -d= -f2 || echo seyalrun_automation)"; do
    [[ -z "$dbname" ]] && continue
    exists=$(dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
      psql -tA -U "$DB_USER" -d postgres \
      -c "SELECT 1 FROM pg_database WHERE datname='${dbname}'" 2>/dev/null | tr -d '[:space:]')
    if [[ "$exists" != "1" ]]; then
      dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
        psql -U "$DB_USER" -d postgres -c "CREATE DATABASE \"${dbname}\"" >/dev/null
      ok "database '${dbname}' created"
    else
      warn "database '${dbname}' already exists — skipping"
    fi
  done
fi

# ── Step 7: Migrations ────────────────────────────────────────────────────────
info "[7/8] Running Alembic migrations..."
run_migration() {
  local svc="$1"
  for i in $(seq 1 10); do
    if dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" \
        run --rm --no-deps "$svc" python -m alembic upgrade head 2>&1 | tail -2; then
      return 0
    fi
    echo "    ${svc} migration attempt ${i} failed — retrying in 3s"
    sleep 3
  done
  fail "${svc} migration failed after 10 attempts"
}

for svc in identity-service inventory-service terminal-service recording-service \
           automation-service zabbix-integration-service metrics-service; do
  echo "    migrating ${svc}..."
  run_migration "$svc"
done
ok "all migrations applied"

# ── Seed superadmin ───────────────────────────────────────────────────────────
dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" \
  run --rm --no-deps identity-service python -m app.seed

# ── Final up -d ───────────────────────────────────────────────────────────────
dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" up -d 2>&1 | tail -5

info "Waiting for all services to be healthy..."
for i in $(seq 1 30); do
  unhealthy=$(dc "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" \
    ps --format "{{.Service}} {{.Health}}" 2>/dev/null \
    | awk '$2!="" && $2!="healthy" {print $1": "$2}')
  [[ -z "$unhealthy" ]] && break
  sleep 2
done

# ── Step 8: Summary ───────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  SeyalRun v2.0 — Deploy Complete"
echo "======================================================"
ok "URL:      https://${EDGE_HOST}:${EDGE_HTTPS_PORT}"
ok "Username: Admin"
ok "Password: seyalrun   <-- default; first login FORCES a password change"
echo ""
echo "  Run verification (after rotating the password in the UI):"
echo "  SUDO_PASSWORD=test SSH_USER=test \\"
echo "    SEYALRUN_ADMIN_PASSWORD='<your-rotated-password>' \\"
echo "    ops/verify-staging.sh ${EDGE_HOST}"
echo "======================================================"
