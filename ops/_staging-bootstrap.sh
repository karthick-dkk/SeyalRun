#!/bin/bash
# Runs ON the staging host (as root, via ops/deploy-staging.sh) inside the
# rsynced repo at /opt/seyalrun-v2. Not meant to be run by hand.
#
# - Generates .env from .env.example with random secrets if missing.
# - Generates a self-signed TLS cert for edge-proxy if missing.
# - DB: always uses the Dockerized Postgres overlay (docker-compose.db.yml).
#   For production, pick Option A or B in .env.example and set DB_HOST before
#   deploying — this script will use whatever DB_HOST is in .env.
# - docker compose build && up -d
# - Runs Alembic migrations + the superadmin seed.
#
# Expects $EDGE_HOST (the staging host's address, for FRONTEND_ORIGIN/CORS)
# in the environment.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${EDGE_HOST:?EDGE_HOST must be set}"

# ── .env ──────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo "[*] Generating .env from .env.example with random secrets..."
  cp .env.example .env
  rand_hex() { openssl rand -hex 32; }

  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(rand_hex)|" .env
  sed -i "s|^SERVICE_JWT_SECRET=.*|SERVICE_JWT_SECRET=$(rand_hex)|" .env
  sed -i "s|^API_TOKEN_PEPPER=.*|API_TOKEN_PEPPER=$(rand_hex)|" .env
  sed -i "s|^ZA_VAULT_PASSWORD=.*|ZA_VAULT_PASSWORD=$(rand_hex)|" .env
  sed -i "s|^ZA_VAULT_SALT=.*|ZA_VAULT_SALT=$(openssl rand -hex 16)|" .env
  sed -i "s|^ZABBIX_WEBHOOK_HMAC_SECRET=.*|ZABBIX_WEBHOOK_HMAC_SECRET=$(rand_hex)|" .env
  sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$(openssl rand -hex 16)|" .env
else
  echo "[*] Reusing existing .env"
fi

# ── TLS cert (self-signed, staging only) ────────────────────────────────────
mkdir -p tls
if [[ ! -f tls/cert.pem || ! -f tls/key.pem ]]; then
  echo "[*] Generating self-signed TLS cert for edge-proxy..."
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout tls/key.pem -out tls/cert.pem \
    -subj "/CN=${EDGE_HOST}" >/dev/null 2>&1
fi
# edge-proxy's nginx runs as an unprivileged, non-root user — the key must be
# world-readable for it to load the cert (self-signed, staging only).
chmod 644 tls/key.pem tls/cert.pem
sed -i "s|^TLS_CERT_PATH=.*|TLS_CERT_PATH=$(pwd)/tls/cert.pem|" .env
sed -i "s|^TLS_KEY_PATH=.*|TLS_KEY_PATH=$(pwd)/tls/key.pem|" .env

# ── CORS origin ──────────────────────────────────────────────────────────────
EDGE_HTTPS_PORT="$(grep '^EDGE_HTTPS_PORT=' .env | cut -d= -f2)"
EDGE_HTTPS_PORT="${EDGE_HTTPS_PORT:-8443}"
sed -i "s|^FRONTEND_ORIGIN=.*|FRONTEND_ORIGIN=https://${EDGE_HOST}:${EDGE_HTTPS_PORT}|" .env

# ── DB engine: always use Dockerized overlay so containers can reach Postgres ─
# Bare-metal Postgres at 127.0.0.1 is unreachable from Docker bridge containers.
# docker-compose.db.yml overlay will re-adopt any existing seyalrun-v2-postgres-1
# container (and its named volume) from a previous deploy.
sed -i "s|^DB_ENGINE=.*|DB_ENGINE=postgres|" .env
if docker inspect seyalrun-v2-postgres-1 &>/dev/null; then
  echo "[*] Re-adopting existing seyalrun-v2-postgres-1 container (DB_HOST=postgres)."
else
  echo "[*] Starting Dockerized Postgres via docker-compose.db.yml overlay."
fi
sed -i "s|^DB_HOST=.*|DB_HOST=postgres|" .env
sed -i "s|^DB_SSLMODE=.*|DB_SSLMODE=disable|" .env
USE_DB_OVERLAY=1

COMPOSE_FILES=(-f docker-compose.yml)
COMPOSE_PROFILE=()
if [[ "$USE_DB_OVERLAY" == "1" ]]; then
  COMPOSE_FILES+=(-f docker-compose.db.yml)
  COMPOSE_PROFILE=(--profile postgres-db)
fi

# Internal TLS is sticky: once the CA exists, every redeploy keeps the overlay.
# This function rebuilds the compose flags from scratch, so without the check a
# routine redeploy would quietly bring the stack back up on plaintext HTTP and
# nothing would report that a security control had been turned off.
INTERNAL_TLS_DIR="${INTERNAL_TLS_DIR:-$(pwd)/tls/internal}"
if [[ -f "${INTERNAL_TLS_DIR}/ca.pem" && -f "${INTERNAL_TLS_DIR}/ca-bundle.pem" ]]; then
  export INTERNAL_TLS_DIR
  COMPOSE_FILES+=(-f docker-compose.internal-tls.yml)
  echo "[*] Internal TLS enabled (found ${INTERNAL_TLS_DIR}/ca.pem)."
else
  echo "[*] Internal TLS not enabled — run ops/gen-internal-tls.sh to encrypt service-to-service traffic."
fi

printf '%s\n' "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" > .compose-flags

# ── Build + start ─────────────────────────────────────────────────────────
# Built one service at a time (not in parallel) and retried on failure: this
# host's network is flaky enough that concurrent large pip/npm downloads
# occasionally hit BrokenPipeError/ECONNRESET.
echo "[*] docker compose build (one service at a time, this can take several minutes)..."
# Only services with a `build:` section (the DB overlay's services are
# image-only and `docker compose build <svc>` errors on those).
BUILD_SERVICES=$(awk '/^  [a-zA-Z0-9_-]+:/{svc=$1} /^    build:/{print svc}' docker-compose.yml | tr -d ':')
for svc in $BUILD_SERVICES; do
  for attempt in 1 2 3; do
    echo "[*] building ${svc} (attempt ${attempt}/3)..."
    if docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" build "${svc}"; then
      break
    elif [[ "$attempt" == 3 ]]; then
      echo "[X] failed to build ${svc} after 3 attempts" >&2
      exit 1
    fi
  done
done

echo "[*] docker compose up -d..."
docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" up -d

# ── Wait for the database to accept connections ──────────────────────────────
if [[ "$USE_DB_OVERLAY" == "1" ]]; then
  echo "[*] Waiting for Dockerized Postgres to become healthy..."
  for i in $(seq 1 30); do
    status=$(docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" ps postgres --format '{{.Health}}' 2>/dev/null || true)
    [[ "$status" == "healthy" ]] && break
    sleep 2
  done

  # /docker-entrypoint-initdb.d only runs against an empty data directory, so
  # if this volume was initialized before INVENTORY_DB_NAME's init script
  # existed (or on any other re-run), create it here too — idempotent.
  DB_USER="$(grep '^DB_USER=' .env | cut -d= -f2)"
  INVENTORY_DB_NAME="$(grep '^INVENTORY_DB_NAME=' .env | cut -d= -f2)"
  echo "[*] Ensuring ${INVENTORY_DB_NAME} database exists..."
  DB_EXISTS=$(docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
    psql -tA -U "$DB_USER" -d postgres -c \
    "SELECT 1 FROM pg_database WHERE datname = '${INVENTORY_DB_NAME}'")
  if [[ "$(echo "$DB_EXISTS" | tr -d '[:space:]')" != "1" ]]; then
    docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
      psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres -c \
      "CREATE DATABASE \"${INVENTORY_DB_NAME}\""
  fi

  TERMINAL_DB_NAME="$(grep '^TERMINAL_DB_NAME=' .env | cut -d= -f2)"
  TERMINAL_DB_NAME="${TERMINAL_DB_NAME:-seyalrun_terminal}"
  echo "[*] Ensuring ${TERMINAL_DB_NAME} database exists..."
  DB_EXISTS=$(docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
    psql -tA -U "$DB_USER" -d postgres -c \
    "SELECT 1 FROM pg_database WHERE datname = '${TERMINAL_DB_NAME}'")
  if [[ "$(echo "$DB_EXISTS" | tr -d '[:space:]')" != "1" ]]; then
    docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
      psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres -c \
      "CREATE DATABASE \"${TERMINAL_DB_NAME}\""
  fi

  AUTOMATION_DB_NAME="$(grep '^AUTOMATION_DB_NAME=' .env | cut -d= -f2)"
  AUTOMATION_DB_NAME="${AUTOMATION_DB_NAME:-seyalrun_automation}"
  echo "[*] Ensuring ${AUTOMATION_DB_NAME} database exists..."
  DB_EXISTS=$(docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
    psql -tA -U "$DB_USER" -d postgres -c \
    "SELECT 1 FROM pg_database WHERE datname = '${AUTOMATION_DB_NAME}'")
  if [[ "$(echo "$DB_EXISTS" | tr -d '[:space:]')" != "1" ]]; then
    docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" exec -T postgres \
      psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres -c \
      "CREATE DATABASE \"${AUTOMATION_DB_NAME}\""
  fi
fi

# ── Alembic migrations (retry — Postgres may still be starting) ─────────────
run_migrations() {
  local service="$1"
  for i in $(seq 1 10); do
    if docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" run --rm --no-deps "$service" python -m alembic upgrade head; then
      return 0
    fi
    echo "    ($service migration attempt $i failed — retrying in 3s)"
    sleep 3
  done
  return 1
}

echo "[*] Running identity-service migrations..."
run_migrations identity-service

echo "[*] Running inventory-service migrations..."
run_migrations inventory-service

echo "[*] Running terminal-service migrations..."
run_migrations terminal-service

echo "[*] Running recording-service migrations..."
run_migrations recording-service

echo "[*] Running automation-service migrations..."
run_migrations automation-service

echo "[*] Running zabbix-integration-service migrations..."
run_migrations zabbix-integration-service

echo "[*] Running metrics-service migrations..."
run_migrations metrics-service

# ── Seed superadmin (idempotent) ─────────────────────────────────────────────
echo "[*] Seeding superadmin user (if not present)..."
docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" run --rm --no-deps identity-service \
  python -m app.seed > seed-output.txt 2>&1 || true
chmod 600 seed-output.txt

# ── Restart so all services see the final .env / are scheduled fresh ────────
echo "[*] docker compose up -d (recreate any pending services)..."
docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" up -d

# Recreated containers report health "starting" until their first probe
# (up to `interval` seconds away) — wait so verify-staging.sh doesn't race it.
echo "[*] Waiting for all services to report healthy..."
for i in $(seq 1 30); do
  unhealthy=$(docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILE[@]}" ps --format "{{.Service}} {{.Health}}" | awk '$2!="" && $2!="healthy" {print $1": "$2}')
  [[ -z "$unhealthy" ]] && break
  sleep 2
done

echo "[OK] Bootstrap complete."
