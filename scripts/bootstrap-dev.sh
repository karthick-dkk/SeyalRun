#!/usr/bin/env bash
# bootstrap-dev.sh — Set up local development environment
set -euo pipefail

echo "=== Pravesh Dev Bootstrap ==="

# Check prerequisites
command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
command -v ansible >/dev/null || { echo "ERROR: ansible not found. Install: brew install ansible"; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker not found"; exit 1; }
command -v pre-commit >/dev/null || { echo "Installing pre-commit..."; brew install pre-commit; }

# Install pre-commit hooks
echo "Installing pre-commit hooks..."
pre-commit install

# Generate secrets baseline (empty — no secrets committed yet)
echo "Generating detect-secrets baseline..."
detect-secrets scan > .secrets.baseline

# Install Python shared library in editable mode
echo "Installing shared library..."
pip3 install -e services/_shared

# Install automation-bridge in editable mode
echo "Installing automation-bridge..."
pip3 install -e "services/automation-bridge[dev]"

# Check Ansible connectivity
echo ""
echo "=== Checking Server Connectivity ==="
echo "Testing Server 1 (JumpServer) at 192.168.64.2..."
if ping -c 1 -W 3 192.168.64.2 >/dev/null 2>&1; then
    echo "  OK: Server 1 reachable"
else
    echo "  WARN: Server 1 not reachable — check VM status"
fi

echo "Testing Server 2 (Zabbix) at 192.168.43.4..."
if ping -c 1 -W 3 192.168.43.4 >/dev/null 2>&1; then
    echo "  OK: Server 2 reachable"
else
    echo "  FAIL: Server 2 NOT reachable — see Phase 0.1 in plan"
fi

echo ""
echo "=== Next Steps ==="
echo "1. Set up SSH keys: ssh-copy-id test@192.168.64.2"
echo "2. Create vault.yml: cp infra/ansible/group_vars/all/vault.yml.example \\"
echo "     infra/ansible/group_vars/all/vault.yml && ansible-vault encrypt \\"
echo "     infra/ansible/group_vars/all/vault.yml"
echo "3. Rotate JumpServer secrets: ansible-playbook -i infra/ansible/inventory/hosts.ini \\"
echo "     infra/ansible/playbooks/jumpserver-config.yml --ask-vault-pass"
echo "4. Harden servers: ansible-playbook -i infra/ansible/inventory/hosts.ini \\"
echo "     infra/ansible/playbooks/server-harden.yml --ask-vault-pass"
echo "5. Install FreeIPA: ansible-playbook -i infra/ansible/inventory/hosts.ini \\"
echo "     infra/ansible/playbooks/freeipa-install.yml --ask-vault-pass"
echo ""
echo "Bootstrap complete!"
