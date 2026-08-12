#!/bin/bash
# Creates all four SeyalRun databases (identity, inventory, terminal,
# automation) on a bare-metal Postgres or MySQL instance configured in .env.
# identity/inventory additionally get schema/<engine>/schema.sql imported
# (idempotent — CREATE TABLE IF NOT EXISTS, safe to re-run); terminal/
# automation have no static schema — their tables are created entirely by
# each service's own Alembic migrations (run `alembic upgrade head` per
# service after this script — printed at the end).
#
# Usage:
#   ops/init-db.sh
#
# Requires .env (DB_ENGINE, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD,
# IDENTITY_DB_NAME, INVENTORY_DB_NAME, TERMINAL_DB_NAME, AUTOMATION_DB_NAME)
# — see .env.example. DB_USER must be able to create databases on DB_HOST.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

: "${DB_ENGINE:?DB_ENGINE must be set in .env (postgres|mysql)}"
: "${DB_HOST:?DB_HOST must be set in .env}"
: "${DB_PORT:?DB_PORT must be set in .env}"
: "${DB_USER:?DB_USER must be set in .env}"
: "${DB_PASSWORD:?DB_PASSWORD must be set in .env}"
: "${IDENTITY_DB_NAME:?IDENTITY_DB_NAME must be set in .env}"
: "${INVENTORY_DB_NAME:?INVENTORY_DB_NAME must be set in .env}"
TERMINAL_DB_NAME="${TERMINAL_DB_NAME:-seyalrun_terminal}"
AUTOMATION_DB_NAME="${AUTOMATION_DB_NAME:-seyalrun_automation}"

case "$DB_ENGINE" in
  postgres)
    export PGPASSWORD="$DB_PASSWORD"
    for db in "$IDENTITY_DB_NAME" "$INVENTORY_DB_NAME" "$TERMINAL_DB_NAME" "$AUTOMATION_DB_NAME"; do
      echo "[*] Ensuring database '$db' exists on ${DB_HOST}:${DB_PORT}..."
      exists=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname = '$db'")
      if [[ "$exists" != "1" ]]; then
        psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c "CREATE DATABASE \"$db\""
      fi
    done
    for db in "$IDENTITY_DB_NAME" "$INVENTORY_DB_NAME"; do
      echo "[*] Importing core/schema/postgres/schema.sql into '$db'..."
      psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$db" -v ON_ERROR_STOP=1 -f core/schema/postgres/schema.sql
    done
    ;;
  mysql)
    for db in "$IDENTITY_DB_NAME" "$INVENTORY_DB_NAME" "$TERMINAL_DB_NAME" "$AUTOMATION_DB_NAME"; do
      echo "[*] Ensuring database '$db' exists on ${DB_HOST}:${DB_PORT}..."
      mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" \
        -e "CREATE DATABASE IF NOT EXISTS \`$db\`"
    done
    for db in "$IDENTITY_DB_NAME" "$INVENTORY_DB_NAME"; do
      echo "[*] Importing core/schema/mysql/schema.sql into '$db'..."
      mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$db" < core/schema/mysql/schema.sql
    done
    ;;
  *)
    echo "Unsupported DB_ENGINE: '$DB_ENGINE' (expected postgres|mysql)" >&2
    exit 1
    ;;
esac

echo "[✓] Databases ready: ${IDENTITY_DB_NAME}, ${INVENTORY_DB_NAME}, ${TERMINAL_DB_NAME}, ${AUTOMATION_DB_NAME} (${DB_ENGINE})."
echo "[i] Now run Alembic migrations for every service to create/update tables:"
echo "    for svc in identity-service inventory-service terminal-service automation-service \\"
echo "               recording-service zabbix-integration-service metrics-service; do"
echo "      docker compose run --rm --no-deps \"\$svc\" python -m alembic upgrade head"
echo "    done"
