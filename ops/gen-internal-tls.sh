#!/bin/bash
# Generates the internal CA and per-service certificates that encrypt
# service-to-service traffic (risk register R-1).
#
#   ops/gen-internal-tls.sh [OUTPUT_DIR]      (default: ./tls/internal)
#
# Idempotent: existing files are kept, so re-running only fills in what is
# missing. Pass FORCE=1 to reissue everything (all services must restart).
#
# This is one-way TLS with an internal CA, not mutual TLS. Client *authentication*
# is already carried by the signed X-Service-Token on every internal endpoint
# (libs/servicetoken, audience-bound, 60s TTL); what was missing is
# confidentiality, since every internal hop was plaintext HTTP. Adding client
# certificates would duplicate authentication at the transport layer and require
# per-service client-cert distribution and rotation for no additional guarantee.
set -euo pipefail

OUT="${1:-./tls/internal}"
DAYS="${DAYS:-825}"          # under the 825-day cap browsers/CAs converged on
FORCE="${FORCE:-}"

# Certificate CN/SAN must match the DNS name callers use, which under Compose is
# the service name. localhost is included because each container's own
# healthcheck connects to itself.
SERVICES=(
  identity-service
  inventory-service
  terminal-service
  recording-service
  api-gateway
  automation-service
  zabbix-integration-service
  metrics-service
)

mkdir -p "$OUT"
cd "$OUT"

# ── CA ────────────────────────────────────────────────────────────────────────
if [[ -n "$FORCE" || ! -f ca.pem || ! -f ca-key.pem ]]; then
  echo "[*] Generating internal CA..."
  openssl req -x509 -nodes -newkey rsa:4096 -days "$DAYS" \
    -keyout ca-key.pem -out ca.pem \
    -subj "/CN=SeyalRun Internal CA/O=SeyalRun" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
  chmod 600 ca-key.pem
  echo "[OK] ca.pem"
else
  echo "[*] Reusing existing ca.pem"
fi

# ── Per-service leaf certificates ─────────────────────────────────────────────
for svc in "${SERVICES[@]}"; do
  if [[ -z "$FORCE" && -f "${svc}.pem" && -f "${svc}-key.pem" ]]; then
    echo "[*] ${svc}: reusing existing certificate"
    continue
  fi
  echo "[*] ${svc}: issuing certificate..."
  openssl req -nodes -newkey rsa:2048 \
    -keyout "${svc}-key.pem" -out "${svc}.csr" \
    -subj "/CN=${svc}/O=SeyalRun" 2>/dev/null

  openssl x509 -req -in "${svc}.csr" \
    -CA ca.pem -CAkey ca-key.pem -CAcreateserial \
    -out "${svc}.pem" -days "$DAYS" \
    -extfile <(printf 'subjectAltName=DNS:%s,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\n' "$svc") \
    2>/dev/null
  rm -f "${svc}.csr"

  # Services run as a non-root uid that does not own these files, so the key has
  # to be world-readable inside the container. It never leaves the host bind
  # mount, and the same trade-off already applies to the edge-proxy cert.
  chmod 644 "${svc}-key.pem" "${svc}.pem"
done

# ── Combined trust bundle ─────────────────────────────────────────────────────
# Points SSL_CERT_FILE at public roots *plus* our CA. Using the internal CA alone
# would make every outbound call to a real service (S3, Elasticsearch, webhooks)
# fail certificate validation.
SYSTEM_CA=""
for candidate in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
  [[ -f "$candidate" ]] && SYSTEM_CA="$candidate" && break
done

{
  if [[ -n "$SYSTEM_CA" ]]; then
    cat "$SYSTEM_CA"
    echo
  else
    echo "[!] No system CA bundle found — the trust bundle will contain only the" >&2
    echo "    internal CA, so outbound calls to public endpoints will fail." >&2
  fi
  cat ca.pem
} > ca-bundle.pem
chmod 644 ca-bundle.pem

echo
echo "[OK] Internal TLS material in $(pwd):"
echo "     ca.pem          internal CA certificate"
echo "     ca-key.pem      CA private key (0600 — back this up, never ship it)"
echo "     <service>.pem   leaf certificate per service"
echo "     ca-bundle.pem   system roots + internal CA, for SSL_CERT_FILE"
echo
echo "Enable with:"
echo "  INTERNAL_TLS_DIR=$(pwd) docker compose \\"
echo "    -f docker-compose.yml -f docker-compose.internal-tls.yml up -d"
