#!/bin/bash
# Re-encrypts every za_credentials.secret_ciphertext under a new
# ZA_VAULT_PASSWORD/ZA_VAULT_SALT, decrypting with the OLD key supplied via
# environment variables.
#
# Usage:
#   1. Edit .env: set ZA_VAULT_PASSWORD/ZA_VAULT_SALT to their NEW values.
#   2. Run, passing the OLD values being rotated away from:
#        OLD_ZA_VAULT_PASSWORD='...' OLD_ZA_VAULT_SALT='...' ops/rotate-vault-key.sh
#
# Runs ops/rotate_vault_key.py inside the inventory-service image so it has
# the same cryptography/SQLAlchemy dependencies as the app.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

: "${OLD_ZA_VAULT_PASSWORD:?Set OLD_ZA_VAULT_PASSWORD to the vault password being rotated away from}"
: "${OLD_ZA_VAULT_SALT:?Set OLD_ZA_VAULT_SALT to the vault salt being rotated away from}"

[[ -f .env ]] || { echo ".env not found — copy .env.example to .env first" >&2; exit 1; }

echo "[*] Rotating za_credentials encryption to the new ZA_VAULT_PASSWORD/ZA_VAULT_SALT in .env..."
docker compose run --rm \
  -e OLD_ZA_VAULT_PASSWORD="$OLD_ZA_VAULT_PASSWORD" \
  -e OLD_ZA_VAULT_SALT="$OLD_ZA_VAULT_SALT" \
  -e DRY_RUN="${DRY_RUN:-}" \
  -e PYTHONPATH=/app \
  -v "$REPO_ROOT/ops/rotate_vault_key.py:/ops/rotate_vault_key.py:ro" \
  --entrypoint python \
  inventory-service /ops/rotate_vault_key.py

echo "[OK] Done. Restart services so they use the new vault key for new writes:"
echo "    docker compose restart inventory-service"
