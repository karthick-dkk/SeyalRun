#!/bin/sh
# Only used by docker-compose.db.yml's optional postgres service, and only on a
# FRESH data volume. POSTGRES_DB already creates IDENTITY_DB_NAME; this creates
# the remaining per-service databases (inventory, terminal, automation) so every
# Phase 1-3 service and metrics-service has its database on first boot.
set -e

for db in "${INVENTORY_DB_NAME}" "${TERMINAL_DB_NAME}" "${AUTOMATION_DB_NAME}"; do
  [ -n "$db" ] || continue
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    SELECT 'CREATE DATABASE "${db}"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${db}')\gexec
EOSQL
done
