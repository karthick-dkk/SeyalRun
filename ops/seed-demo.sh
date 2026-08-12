#!/bin/bash
# Populate a running SeyalRun stack with obviously-fake demo data, so an evaluator
# sees a working system instead of an empty login page.
#
#   ops/seed-demo.sh
#
# Safe to re-run: prior demo rows are replaced. Refuses to touch a deployment that
# already holds non-demo hosts or credentials — override with SEYALRUN_DEMO_FORCE=1
# only if you are certain.
#
# Runs inside inventory-service because that container already has libs/, the
# database and the vault key. Audit rows go through identity-service's HTTP
# endpoint so they are properly sequenced and hash-chained: /audit/verify then
# returns a real result rather than a staged one.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE_FLAGS=()
if [[ -f .compose-flags ]]; then
  while IFS= read -r flag; do [[ -n "$flag" ]] && COMPOSE_FLAGS+=("$flag"); done < .compose-flags
fi

# .compose-flags may include the internal-TLS overlay, whose volume paths are
# built from INTERNAL_TLS_DIR. Reusing the flags without also providing that
# variable makes compose resolve those mounts to empty strings — noisy at best,
# and a wrong bind mount if it ever recreates a container rather than exec'ing
# into a running one. Same default as ops/_staging-bootstrap.sh.
export INTERNAL_TLS_DIR="${INTERNAL_TLS_DIR:-$(pwd)/tls/internal}"

if ! docker compose "${COMPOSE_FLAGS[@]}" ps inventory-service 2>/dev/null | grep -q "Up\|running"; then
  echo "[X] inventory-service is not running. Start the stack first." >&2
  exit 1
fi

echo "[*] Seeding demo data..."
docker compose "${COMPOSE_FLAGS[@]}" exec -T \
  -e SEYALRUN_DEMO_FORCE="${SEYALRUN_DEMO_FORCE:-}" \
  inventory-service python - < ops/seed_demo.py

echo
echo "Open the UI and sign in. Things worth showing an evaluator:"
echo "  * six hosts in the inventory, all in the reserved 203.0.113.0/24 test range"
echo "  * a hash-chained audit timeline covering logins, a session, and a job"
echo "  * GET /api/v1/audit/verify — proves the chain is unbroken back to genesis"
