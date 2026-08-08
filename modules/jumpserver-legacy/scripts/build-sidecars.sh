#!/usr/bin/env bash
# build-sidecars.sh — Build all sidecar Docker images on the server (ARM64)
# Run this ON the server: bash /opt/pravesh/scripts/build-sidecars.sh
set -euo pipefail

SERVICES=(automation-bridge zbx-sync launch-token webhook-receiver)
BUILD_DIR=/opt/pravesh/services

echo "=== Building SeyalRun sidecars (ARM64) ==="

for svc in "${SERVICES[@]}"; do
    echo ""
    echo "--- Building: $svc ---"

    # Each Dockerfile is built from the services/ directory as context
    docker build \
        --platform linux/arm64 \
        --build-arg GIT_SHA="$(git -C /opt/pravesh rev-parse --short HEAD 2>/dev/null || echo 'unknown')" \
        -f "${BUILD_DIR}/${svc}/Dockerfile" \
        -t "pravesh/${svc}:latest" \
        "${BUILD_DIR}" \
        2>&1 | tail -5

    echo "  pravesh/${svc}:latest built OK"
done

echo ""
echo "=== Build complete ==="
docker images | grep pravesh
