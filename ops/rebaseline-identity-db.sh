#!/bin/bash
# Re-baseline the identity database, discarding an unverifiable audit chain.
#
#   bash ops/rebaseline-identity-db.sh            # prompts before destroying
#   REBASELINE_CONFIRM=yes bash ops/...           # non-interactive
#
# WHY THIS EXISTS (R-9). Until 2026-08-09 log_action hashed session_id and
# result into the entry payload but never persisted those columns, so
# verify_chain() rebuilt a payload missing keys the stored hash covered and
# reported "row contents were altered" for rows nobody had touched. Those rows
# cannot be repaired: the hashed values were never stored, and migration 022's
# immutability trigger blocks UPDATE by design. An environment carrying them can
# never present its audit log as evidence, so the only remedy is a fresh chain.
#
# THIS DESTROYS THE IDENTITY DATABASE: every user, group, role, API token and
# audit row. Inventory, sessions, recordings and automation are untouched (they
# live in their own databases). A superadmin is reseeded and its password
# printed once. Run it on staging/demo hosts. Do not run it on a production
# system whose audit log is already trustworthy — check first:
#
#   bash ops/rebaseline-identity-db.sh --check    # verify only, change nothing
set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[->]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[X]${NC} $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[[ -f .env ]] || fail ".env not found in $REPO_ROOT — is the stack deployed here?"
set -a; source .env; set +a
: "${DB_USER:?}" ; : "${DB_PASSWORD:?}" ; : "${IDENTITY_DB_NAME:?}"
[[ "${DB_ENGINE:-postgres}" == "postgres" ]] || fail "only DB_ENGINE=postgres is supported here; MySQL needs the equivalent DROP/CREATE by hand"

CHECK_ONLY="false"
[[ "${1:-}" == "--check" ]] && CHECK_ONLY="true"

COMPOSE_FLAGS=()
if [[ -f .compose-flags ]]; then
  while IFS= read -r flag; do [[ -n "$flag" ]] && COMPOSE_FLAGS+=("$flag"); done < .compose-flags
fi
# The internal-TLS overlay builds its volume paths from this; without it compose
# resolves those mounts to empty strings. Same default as ops/_staging-bootstrap.sh.
export INTERNAL_TLS_DIR="${INTERNAL_TLS_DIR:-$(pwd)/tls/internal}"

dc() { docker compose "${COMPOSE_FLAGS[@]}" "$@"; }
psql_() { dc exec -T -e PGPASSWORD="$DB_PASSWORD" postgres psql -U "$DB_USER" "$@"; }

# ── Verify, don't count ──────────────────────────────────────────────────────
# Reads every chained row, rebuilds each payload exactly as the writer did, and
# runs the real verify_chain(). Counting hashed rows is what hid R-9 for weeks;
# this asks the chain the actual question.
CHAIN_CHECK=$(cat <<'PY'
import asyncio, sys
sys.path.insert(0, "/app")
from sqlalchemy import select
from app.database import SessionLocal
from app.models import ZAAuditLog
from app.audit import audit_payload
from libs.audithash import verify_chain

async def main():
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(ZAAuditLog).where(ZAAuditLog.seq.isnot(None)).order_by(ZAAuditLog.seq)
        )).scalars().all()
        result = verify_chain([{
            "seq": r.seq, "prev_hash": r.prev_hash, "entry_hash": r.entry_hash,
            "payload": audit_payload(r.user_id, r.username, r.action, r.resource_type,
                                     r.resource_id, r.details, r.ip_address, r.created_at,
                                     session_id=r.session_id, result=r.result),
        } for r in rows])
        print(f"rows={len(rows)} verify_chain={result}")
        sys.exit(0 if result["ok"] else 1)

asyncio.run(main())
PY
)

verify_now() { dc exec -T identity-service python - <<< "$CHAIN_CHECK"; }

info "Current chain state:"
if verify_now; then
  ok "chain verifies"
  [[ "$CHECK_ONLY" == "true" ]] && exit 0
  warn "This chain is intact. Re-baselining would destroy a trustworthy audit log."
  warn "If you are sure, re-run with REBASELINE_FORCE=1."
  [[ "${REBASELINE_FORCE:-}" == "1" ]] || exit 1
else
  warn "chain does NOT verify (this is what the re-baseline is for)"
  [[ "$CHECK_ONLY" == "true" ]] && exit 1
fi

# ── Confirm ─────────────────────────────────────────────────────────────────
echo
warn "About to DROP database '${IDENTITY_DB_NAME}'."
warn "Every user, group, role, API token and audit row in it is destroyed."
if [[ "${REBASELINE_CONFIRM:-}" != "yes" ]]; then
  read -r -p "Type the database name to confirm: " typed
  [[ "$typed" == "$IDENTITY_DB_NAME" ]] || fail "aborted (got '${typed}')"
fi

# ── Backup ──────────────────────────────────────────────────────────────────
# Through the container, because a Dockerized-Postgres deployment has no host
# pg_dump. pipefail is already on, so a dump failure aborts instead of leaving
# a plausible-looking empty archive behind.
mkdir -p backups
BACKUP="backups/${IDENTITY_DB_NAME}-prebaseline-$(date +%Y%m%d-%H%M%S).sql.gz"
info "Backing up to ${BACKUP}..."
dc exec -T -e PGPASSWORD="$DB_PASSWORD" postgres pg_dump -U "$DB_USER" -d "$IDENTITY_DB_NAME" | gzip > "$BACKUP"
[[ -s "$BACKUP" && $(gzip -t "$BACKUP" 2>&1; echo $?) -eq 0 ]] || fail "backup is empty or corrupt — refusing to drop"
ok "backup written ($(du -h "$BACKUP" | cut -f1))"

# ── Swap ────────────────────────────────────────────────────────────────────
info "Stopping identity-service so nothing writes during the swap..."
dc stop identity-service >/dev/null

info "Dropping and recreating ${IDENTITY_DB_NAME}..."
psql_ -d postgres -c "DROP DATABASE IF EXISTS ${IDENTITY_DB_NAME} WITH (FORCE);" >/dev/null
psql_ -d postgres -c "CREATE DATABASE ${IDENTITY_DB_NAME} OWNER ${DB_USER};" >/dev/null
ok "fresh database created"

info "Running migrations to head..."
dc run --rm --no-deps identity-service python -m alembic upgrade head >/dev/null
ok "migrations at head"

info "Seeding superadmin..."
dc run --rm --no-deps identity-service python -m app.seed 2>&1 | tee seed-output.txt
chmod 600 seed-output.txt
warn "The password above is shown once. Save it now; seed-output.txt is 0600 and should be deleted."

info "Starting the stack back up..."
dc up -d >/dev/null
for _ in $(seq 1 30); do
  unhealthy=$(dc ps --format "{{.Service}} {{.Health}}" | awk '$2!="" && $2!="healthy" {print $1}')
  [[ -z "$unhealthy" ]] && break
  sleep 2
done

# ── Prove it ────────────────────────────────────────────────────────────────
info "Verifying the new chain..."
verify_now || fail "the fresh chain does not verify — this is a bug, not a data problem"
ok "re-baseline complete; the audit chain verifies from genesis"
