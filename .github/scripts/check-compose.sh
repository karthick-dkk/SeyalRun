#!/bin/bash
# Renders every supported compose combination with an .env built the way the
# installer builds one, and checks that Redis fails closed without a password.
# Runs in a scratch copy, so it never touches a developer's real .env.
#
#   bash .github/scripts/check-compose.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cp docker-compose*.yml .env.example "$work/"
cp -R ops "$work/ops"
cd "$work"
cp .env.example .env
bash ops/ensure-redis-password.sh .env >/dev/null
sed -i.bak -e 's|^TLS_CERT_PATH=.*|TLS_CERT_PATH=/tmp/cert.pem|' \
           -e 's|^TLS_KEY_PATH=.*|TLS_KEY_PATH=/tmp/key.pem|' .env
export INTERNAL_TLS_DIR="$work/tls/internal"

for combo in "docker-compose.yml" \
             "docker-compose.yml docker-compose.db.yml" \
             "docker-compose.yml docker-compose.db.yml docker-compose.internal-tls.yml" \
             "docker-compose.prod.yml" \
             "docker-compose.prod.yml docker-compose.db.yml" \
             "docker-compose.prod.yml docker-compose.db.yml docker-compose.internal-tls.yml"; do
  args=(); for f in $combo; do args+=(-f "$f"); done
  docker compose "${args[@]}" --profile postgres-db config -q
  echo "ok: $combo"
done

sed -i.bak 's|^REDIS_PASSWORD=.*|REDIS_PASSWORD=|' .env
if docker compose -f docker-compose.prod.yml config -q 2>/dev/null; then
  echo "docker-compose.prod.yml rendered with an empty REDIS_PASSWORD — Redis would start open" >&2
  exit 1
fi
echo "ok: compose refuses to start Redis without REDIS_PASSWORD"
