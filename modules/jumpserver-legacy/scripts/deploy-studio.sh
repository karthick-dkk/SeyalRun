#!/bin/bash
# Deploy Playbook Studio + SeyalRun Console
# Run on lab host: 192.168.64.2
# Usage: ./scripts/deploy-studio.sh

set -euo pipefail

HOST="192.168.64.2"
DEPLOY_DIR="/opt/pravesh"
COMPOSE_FILE="docker/compose.studio.yml"

echo "=== SeyalRun Studio Deployment ==="
echo "Host: $HOST"
echo ""

# 1. Ensure /playbooks/studio dir exists on host
echo "[1/6] Preparing playbook directory..."
ssh test@$HOST "sudo mkdir -p /playbooks/studio && sudo chmod 777 /playbooks/studio"

# 2. Sync source files
echo "[2/6] Syncing source files..."
rsync -az --exclude='node_modules' --exclude='__pycache__' --exclude='.git' \
    services/playbook-studio/ test@$HOST:$DEPLOY_DIR/services/playbook-studio/
rsync -az --exclude='node_modules' --exclude='dist' \
    services/seyalrun-console/ test@$HOST:$DEPLOY_DIR/services/seyalrun-console/
rsync -az docker/ test@$HOST:$DEPLOY_DIR/docker/

# 3. Build images on host (ARM64)
echo "[3/6] Building playbook-studio image..."
ssh test@$HOST "cd $DEPLOY_DIR && docker build -t seyalrun/playbook-studio:latest services/playbook-studio/"

echo "[4/6] Building seyalrun-console image..."
ssh test@$HOST "cd $DEPLOY_DIR && docker build -t pravesh/seyalrun-console:latest services/seyalrun-console/"

# 4. Rebuild automation-bridge with inline route
echo "[5/6] Rebuilding automation-bridge..."
rsync -az services/automation-bridge/ test@$HOST:$DEPLOY_DIR/services/automation-bridge/
ssh test@$HOST "cd $DEPLOY_DIR && docker build -t seyalrun/automation-bridge:latest services/automation-bridge/ && \
    docker compose -f docker/compose.sidecars.yml up -d --no-deps automation-bridge"

# 5. Start studio services
echo "[6/6] Starting Playbook Studio services..."
ssh test@$HOST "cd $DEPLOY_DIR && docker compose -f docker/compose.studio.yml up -d"

echo ""
echo "=== Deployment complete! ==="
echo "  Playbook Studio API: http://$HOST:8005"
echo "  SeyalRun Console UI:  http://$HOST:3000"
echo ""
echo "Verify:"
echo "  curl -s http://$HOST:8005/health"
echo "  curl -s http://$HOST:3000/"
echo "  curl -s http://$HOST:8005/api/v1/modules | jq '.total'"
