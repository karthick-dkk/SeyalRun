#!/usr/bin/env bash
# SeyalRun v2.0 — one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/karthick-dkk/SeyalRun/main/install.sh | bash
#
# Pulls prebuilt images from Docker Hub (docker.io/karthickdk02/seyalrun-*) —
# no source code, no local build. Fully non-interactive: every choice has a
# sane default and can be overridden by exporting a variable before running,
# e.g.:
#
#   SEYALRUN_HOST=seyalrun.example.com \
#   SEYALRUN_VERSION=2.0.0 \
#   FRAME_ANCESTORS=https://zabbix.example.com \
#     curl -fsSL https://raw.githubusercontent.com/karthick-dkk/SeyalRun/main/install.sh | bash
#
# Safe to re-run: an existing .env / TLS cert / database is reused, not
# regenerated or overwritten.

set -euo pipefail

REPO_RAW_BASE="${SEYALRUN_REPO_RAW_BASE:-https://raw.githubusercontent.com/karthick-dkk/SeyalRun/main}"
INSTALL_DIR="${SEYALRUN_DIR:-$PWD/seyalrun}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[->]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[X]${NC} $*"; exit 1; }

echo ""
echo "======================================================"
echo "  SeyalRun v2.0 — Installer"
echo "======================================================"

# ── Step 0: platform detection and prerequisites ──────────────────────────────
info "[0/8] Detecting platform..."

# Architecture. Images are published for linux/amd64 and linux/arm64 only, so a
# machine that is neither must be told now rather than after eight steps of
# setup — docker pull would otherwise fail with a manifest error that reads like
# a network problem.
case "$(uname -m)" in
  x86_64|amd64)          ARCH="amd64" ;;
  aarch64|arm64)         ARCH="arm64" ;;
  *) fail "unsupported architecture '$(uname -m)'. SeyalRun publishes linux/amd64 and linux/arm64." ;;
esac

# Distribution family. ID_LIKE is checked as well as ID because derivatives
# (Rocky, Alma, Oracle, Pop!_OS) name themselves but declare the family they
# behave like, and matching only on ID means every new derivative is unsupported
# until someone adds it by hand.
OS_ID="linux"; OS_NAME="unknown"; PKG=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-linux}"; OS_NAME="${PRETTY_NAME:-$OS_ID}"
  case " ${ID:-} ${ID_LIKE:-} " in
    *" debian "*|*" ubuntu "*)                      PKG="apt" ;;
    *" rhel "*|*" fedora "*|*" centos "*)           PKG="dnf" ;;
    *" amzn "*)                                     PKG="dnf" ;;
    *" suse "*|*" opensuse "*)                      PKG="zypper" ;;
  esac
  # Amazon Linux 2 predates dnf; RHEL/CentOS 7 likewise.
  [ "$PKG" = "dnf" ] && ! command -v dnf >/dev/null 2>&1 && PKG="yum"
fi
ok "platform: ${OS_NAME} (${ARCH})"

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

pkg_install() {
  case "$PKG" in
    apt)    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "$@" ;;
    dnf)    $SUDO dnf install -y -q "$@" ;;
    yum)    $SUDO yum install -y -q "$@" ;;
    zypper) $SUDO zypper --non-interactive install "$@" ;;
    *)      return 1 ;;
  esac
}

info "[0/8] Checking prerequisites..."

# openssl first: it is in every base repo, so needing it is never a reason to
# reach for Docker's installer.
if ! command -v openssl >/dev/null 2>&1; then
  info "openssl missing — installing from the distribution repository..."
  pkg_install openssl || fail "could not install openssl automatically. Install it and re-run."
fi

# Docker. Installed automatically from the DISTRIBUTION's packages, not by
# piping get.docker.com into a shell: the convenience script adds Docker's own
# apt/yum repository and runs unreviewed remote code as root, which is a large
# thing to do silently inside another installer. Set SEYALRUN_NO_INSTALL=1 to
# skip all of this and be told what to install instead.
if ! command -v docker >/dev/null 2>&1; then
  if [ -n "${SEYALRUN_NO_INSTALL:-}" ] || [ -z "$PKG" ]; then
    fail "docker not found. Install Docker: https://docs.docker.com/engine/install/"
  fi
  info "docker missing — installing from ${OS_NAME} packages..."
  case "$PKG" in
    apt)         pkg_install docker.io docker-compose-v2 || pkg_install docker.io ;;
    dnf|yum)     pkg_install docker docker-compose-plugin || pkg_install docker ;;
    zypper)      pkg_install docker docker-compose ;;
  esac
  command -v docker >/dev/null 2>&1 ||     fail "automatic Docker install did not succeed. Install it: https://docs.docker.com/engine/install/"
  # Package installs leave the service down on RHEL-family and Amazon Linux.
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
fi

# Compose v2 is a plugin and may be absent even where docker is present.
if ! docker compose version >/dev/null 2>&1; then
  if [ -n "${SEYALRUN_NO_INSTALL:-}" ] || [ -z "$PKG" ]; then
    fail "docker compose (v2 plugin) not found. Install it: https://docs.docker.com/compose/install/"
  fi
  info "docker compose v2 missing — installing..."
  case "$PKG" in
    apt)     pkg_install docker-compose-v2 || true ;;
    dnf|yum) pkg_install docker-compose-plugin || true ;;
    zypper)  pkg_install docker-compose ;;
  esac
  docker compose version >/dev/null 2>&1 ||     fail "docker compose v2 still unavailable. Install it: https://docs.docker.com/compose/install/"
fi

# Reachable, not merely installed: a stopped daemon or a user outside the docker
# group both look exactly like a working install until the first pull fails.
if ! docker info >/dev/null 2>&1; then
  $SUDO systemctl start docker >/dev/null 2>&1 || true
  docker info >/dev/null 2>&1 || fail \
    "docker is installed but not reachable. Start it ('systemctl start docker'), and if you are not root add yourself to the docker group ('usermod -aG docker $USER') then log out and back in."
fi

ok "docker, docker compose, openssl all present (${OS_NAME}, ${ARCH})"

# ── Step 1: install directory ─────────────────────────────────────────────────
info "[1/8] Setting up ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cd "${INSTALL_DIR}"
ok "using ${INSTALL_DIR}"

# ── Step 2: download compose files ────────────────────────────────────────────
# A file already present is used as-is and not re-fetched, so an operator can
# stage everything (scp/rsync) and run this offline — the same hosts that cannot
# reach Docker Hub usually cannot reach GitHub either.
info "[2/8] Fetching deploy files from ${REPO_RAW_BASE} (staged files are kept)..."
fetch() {
  [[ -f "$2" ]] && { echo "  using staged $2"; return 0; }
  curl -fsSL "${REPO_RAW_BASE}/$1" -o "$2"
}
fetch "docker-compose.prod.yml" "docker-compose.yml"
fetch "docker-compose.db.yml"   "docker-compose.db.yml"
fetch ".env.example"            ".env.example"
# The DB compose mounts ./core/docker-init/<engine> — the init script must land
# there, not at a bare docker-init/, or the per-service databases are never
# created and every migration then fails against a database that does not exist.
mkdir -p core/docker-init/postgres core/docker-init/mysql ops schema/postgres schema/mysql
fetch "core/docker-init/postgres/init-dbs.sh" "core/docker-init/postgres/init-dbs.sh"
fetch "core/docker-init/mysql/init-dbs.sh"    "core/docker-init/mysql/init-dbs.sh"
chmod +x core/docker-init/postgres/init-dbs.sh core/docker-init/mysql/init-dbs.sh 2>/dev/null || true
if [[ -n "${SEYALRUN_DB_HOST:-}" ]]; then
  # External/bare-metal DB mode — need the same DB-bootstrap script and
  # schema files the source-tree Quickstart uses (see ops/init-db.sh).
  fetch "ops/init-db.sh"              "ops/init-db.sh"
  fetch "schema/postgres/schema.sql"  "schema/postgres/schema.sql"
  fetch "schema/mysql/schema.sql"     "schema/mysql/schema.sql"
  chmod +x ops/init-db.sh
fi
ok "deploy files ready"

# ── Step 3: generate .env ─────────────────────────────────────────────────────
info "[3/8] Generating .env..."
if [[ -f .env ]]; then
  warn ".env already exists — reusing (delete it for a truly fresh install)"
else
  cp .env.example .env
  rand_hex() { openssl rand -hex 32; }
  sed -i.bak "s|^JWT_SECRET=.*|JWT_SECRET=$(rand_hex)|" .env
  sed -i.bak "s|^SERVICE_JWT_SECRET=.*|SERVICE_JWT_SECRET=$(rand_hex)|" .env
  sed -i.bak "s|^API_TOKEN_PEPPER=.*|API_TOKEN_PEPPER=$(rand_hex)|" .env
  sed -i.bak "s|^ZA_VAULT_PASSWORD=.*|ZA_VAULT_PASSWORD=$(rand_hex)|" .env
  sed -i.bak "s|^ZA_VAULT_SALT=.*|ZA_VAULT_SALT=$(openssl rand -hex 16)|" .env
  sed -i.bak "s|^ZABBIX_WEBHOOK_HMAC_SECRET=.*|ZABBIX_WEBHOOK_HMAC_SECRET=$(rand_hex)|" .env
  sed -i.bak "s|^DB_PASSWORD=.*|DB_PASSWORD=$(openssl rand -hex 16)|" .env
  rm -f .env.bak
  ok ".env generated with random secrets"
fi

# ── Step 4: TLS certificate ───────────────────────────────────────────────────
info "[4/8] Setting up TLS certificate..."
SEYALRUN_HOST="${SEYALRUN_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
SEYALRUN_HOST="${SEYALRUN_HOST:-$(hostname -f 2>/dev/null)}"
SEYALRUN_HOST="${SEYALRUN_HOST:-localhost}"
mkdir -p tls
if [[ ! -f tls/cert.pem || ! -f tls/key.pem ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout tls/key.pem -out tls/cert.pem \
    -subj "/CN=${SEYALRUN_HOST}" >/dev/null 2>&1
  ok "self-signed TLS cert generated for ${SEYALRUN_HOST}"
  warn "self-signed — browsers will warn on first visit. Bring your own cert via TLS_CERT_PATH/TLS_KEY_PATH in .env for production use."
else
  warn "TLS cert already exists — reusing"
fi
chmod 644 tls/key.pem tls/cert.pem
sed -i.bak "s|^TLS_CERT_PATH=.*|TLS_CERT_PATH=$(pwd)/tls/cert.pem|" .env
sed -i.bak "s|^TLS_KEY_PATH=.*|TLS_KEY_PATH=$(pwd)/tls/key.pem|" .env
rm -f .env.bak

# ── Step 5: DB + CORS + Zabbix-embed config ───────────────────────────────────
info "[5/8] Configuring database and origins..."
EDGE_HTTPS_PORT="$(grep '^EDGE_HTTPS_PORT=' .env | cut -d= -f2)"
EDGE_HTTPS_PORT="${EDGE_HTTPS_PORT:-8443}"
sed -i.bak "s|^FRONTEND_ORIGIN=.*|FRONTEND_ORIGIN=https://${SEYALRUN_HOST}:${EDGE_HTTPS_PORT}|" .env
# Pin the image tag when asked. Without this the compose default (:latest)
# wins and a reinstall silently moves to whatever the newest stable release
# is — which is exactly what an operator pinning a version is trying to avoid,
# and what makes "what was running on this date" unanswerable.
if [[ -n "${SEYALRUN_VERSION:-}" ]]; then
  if grep -q '^SEYALRUN_VERSION=' .env; then
    sed -i.bak "s|^SEYALRUN_VERSION=.*|SEYALRUN_VERSION=${SEYALRUN_VERSION}|" .env
  else
    echo "SEYALRUN_VERSION=${SEYALRUN_VERSION}" >> .env
  fi
  ok "pinned image tag: ${SEYALRUN_VERSION}"
fi
if [[ -n "${FRAME_ANCESTORS:-}" ]]; then
  sed -i.bak "s|^FRAME_ANCESTORS=.*|FRAME_ANCESTORS=${FRAME_ANCESTORS}|" .env
fi

if [[ -n "${SEYALRUN_DB_HOST:-}" ]]; then
  # ── External / bare-metal database (existing Postgres or MySQL you already run) ──
  : "${SEYALRUN_DB_USER:?SEYALRUN_DB_USER must be set when SEYALRUN_DB_HOST is used}"
  : "${SEYALRUN_DB_PASSWORD:?SEYALRUN_DB_PASSWORD must be set when SEYALRUN_DB_HOST is used}"
  DB_ENGINE_VAL="${SEYALRUN_DB_ENGINE:-postgres}"
  DB_PORT_VAL="${SEYALRUN_DB_PORT:-$([[ "$DB_ENGINE_VAL" == mysql ]] && echo 3306 || echo 5432)}"
  sed -i.bak "s|^DB_ENGINE=.*|DB_ENGINE=${DB_ENGINE_VAL}|" .env
  sed -i.bak "s|^DB_HOST=.*|DB_HOST=${SEYALRUN_DB_HOST}|" .env
  sed -i.bak "s|^DB_PORT=.*|DB_PORT=${DB_PORT_VAL}|" .env
  sed -i.bak "s|^DB_USER=.*|DB_USER=${SEYALRUN_DB_USER}|" .env
  sed -i.bak "s|^DB_PASSWORD=.*|DB_PASSWORD=${SEYALRUN_DB_PASSWORD}|" .env
  sed -i.bak "s|^DB_SSLMODE=.*|DB_SSLMODE=${SEYALRUN_DB_SSLMODE:-require}|" .env
  rm -f .env.bak
  COMPOSE=(docker compose -f docker-compose.yml)
  ok "database: external ${DB_ENGINE_VAL} at ${SEYALRUN_DB_HOST}:${DB_PORT_VAL}"

  info "Creating databases + importing schema on ${SEYALRUN_DB_HOST}..."
  ops/init-db.sh
  ok "external database ready"
else
  # ── Dockerized database (default — no bare-metal DB needed) ──
  sed -i.bak "s|^DB_ENGINE=.*|DB_ENGINE=${SEYALRUN_DB_ENGINE:-postgres}|" .env
  sed -i.bak "s|^DB_HOST=.*|DB_HOST=${SEYALRUN_DB_ENGINE:-postgres}|" .env
  sed -i.bak "s|^DB_SSLMODE=.*|DB_SSLMODE=disable|" .env
  rm -f .env.bak
  DB_PROFILE="${SEYALRUN_DB_ENGINE:-postgres}-db"
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.db.yml --profile "${DB_PROFILE}")
  ok "database engine: ${SEYALRUN_DB_ENGINE:-postgres} (Dockerized)"
fi

# ── Step 6: load or pull images ───────────────────────────────────────────────
# Air-gapped / restricted hosts cannot reach Docker Hub. Point
# SEYALRUN_IMAGE_ARCHIVE at a `docker save | gzip` tarball produced on a machine
# that CAN pull (same architecture as this host), and the images are loaded from
# it instead of pulled. This is the path a locked-down staging box needs — the
# host only has to reach the file, not the registry.
if [[ -n "${SEYALRUN_IMAGE_ARCHIVE:-}" ]]; then
  [[ -f "$SEYALRUN_IMAGE_ARCHIVE" ]] || fail "SEYALRUN_IMAGE_ARCHIVE=$SEYALRUN_IMAGE_ARCHIVE not found"
  info "[6/8] Loading images from ${SEYALRUN_IMAGE_ARCHIVE} (offline)..."
  if [[ "$SEYALRUN_IMAGE_ARCHIVE" == *.gz ]]; then
    gunzip -c "$SEYALRUN_IMAGE_ARCHIVE" | docker load
  else
    docker load -i "$SEYALRUN_IMAGE_ARCHIVE"
  fi
  ok "images loaded from archive"
else
  info "[6/8] Pulling images (this takes a few minutes on first run)..."
  "${COMPOSE[@]}" pull
  ok "images pulled"
fi

if [[ -n "${SEYALRUN_DB_HOST:-}" ]]; then
  info "Starting redis..."
  "${COMPOSE[@]}" up -d redis
else
  info "Starting database + redis..."
  "${COMPOSE[@]}" up -d redis "${SEYALRUN_DB_ENGINE:-postgres}"
  info "Waiting for database to be healthy..."
  for i in $(seq 1 30); do
    status=$("${COMPOSE[@]}" ps "${SEYALRUN_DB_ENGINE:-postgres}" --format '{{.Health}}' 2>/dev/null || true)
    [[ "$status" == "healthy" ]] && break
    sleep 2
  done
  [[ "$status" == "healthy" ]] || fail "database did not become healthy — check: ${COMPOSE[*]} logs ${SEYALRUN_DB_ENGINE:-postgres}"
  ok "database healthy"
fi

# ── Step 7: migrations + seed ─────────────────────────────────────────────────
info "[7/8] Running migrations..."
run_migration() {
  local svc="$1"
  for i in $(seq 1 10); do
    if "${COMPOSE[@]}" run --rm --no-deps "$svc" python -m alembic upgrade head 2>&1 | tail -2; then
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

info "Seeding superadmin account..."
"${COMPOSE[@]}" run --rm --no-deps identity-service python -m app.seed
ok "superadmin ready"

# ── Step 8: bring the full stack up ───────────────────────────────────────────
info "[8/8] Starting all services..."
"${COMPOSE[@]}" up -d
info "Waiting for all services to be healthy..."
for i in $(seq 1 30); do
  unhealthy=$("${COMPOSE[@]}" ps --format "{{.Service}} {{.Health}}" 2>/dev/null \
    | awk '$2!="" && $2!="healthy" {print $1": "$2}')
  [[ -z "$unhealthy" ]] && break
  sleep 2
done

echo ""
echo "======================================================"
echo "  SeyalRun v2.0 — Install Complete"
echo "======================================================"
ok "Directory: ${INSTALL_DIR}"
ok "URL:       https://${SEYALRUN_HOST}:${EDGE_HTTPS_PORT}"
ok "Username:  Admin"
ok "Password:  seyalrun   <-- default; first login FORCES a password change"
echo ""
echo "  Manage it:"
echo "    cd ${INSTALL_DIR}"
echo "    ${COMPOSE[*]} ps"
echo "    ${COMPOSE[*]} logs -f"
echo "======================================================"
