#!/bin/bash
# Only used by docker-compose.db.yml's optional mysql service.
# Creates every per-service database (identity, inventory, terminal, automation)
# and grants DB_USER access to each, so all Phase 1-3 services + metrics work.
set -e

mysql -u root -p"$MYSQL_ROOT_PASSWORD" <<-EOSQL
  CREATE DATABASE IF NOT EXISTS \`${IDENTITY_DB_NAME}\`;
  CREATE DATABASE IF NOT EXISTS \`${INVENTORY_DB_NAME}\`;
  CREATE DATABASE IF NOT EXISTS \`${TERMINAL_DB_NAME}\`;
  CREATE DATABASE IF NOT EXISTS \`${AUTOMATION_DB_NAME}\`;
  GRANT ALL PRIVILEGES ON \`${IDENTITY_DB_NAME}\`.* TO '${MYSQL_USER}'@'%';
  GRANT ALL PRIVILEGES ON \`${INVENTORY_DB_NAME}\`.* TO '${MYSQL_USER}'@'%';
  GRANT ALL PRIVILEGES ON \`${TERMINAL_DB_NAME}\`.* TO '${MYSQL_USER}'@'%';
  GRANT ALL PRIVILEGES ON \`${AUTOMATION_DB_NAME}\`.* TO '${MYSQL_USER}'@'%';
  FLUSH PRIVILEGES;
EOSQL
