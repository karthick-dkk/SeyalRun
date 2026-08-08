#!/bin/bash
# Renders modules/zabbix/monitoring/template-source.yaml (Zabbix 6.4 export format) into
# modules/zabbix/monitoring/zabbix-templates/7.0/ and /8.0/, each importable as-is.
#
# The 6.4 export schema used in template-source.yaml (template_groups,
# discovery_rules, item/trigger prototypes, ZABBIX_ACTIVE) is read
# unchanged by Zabbix 7.0 and 8.0 — the only required change is the
# top-level zabbix_export.version string.
#
# Usage:
#   ops/render-zabbix-templates.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SRC="modules/zabbix/monitoring/template-source.yaml"
[[ -f "$SRC" ]] || { echo "Missing $SRC" >&2; exit 1; }

for ver in 7.0 8.0; do
  OUT_DIR="modules/zabbix/monitoring/zabbix-templates/${ver}"
  OUT_FILE="${OUT_DIR}/seyalrun-platform.yaml"
  mkdir -p "$OUT_DIR"
  sed "s/^  version: '6.4'/  version: '${ver}'/" "$SRC" > "$OUT_FILE"
  echo "[*] Wrote $OUT_FILE"
done

echo "[✓] Rendered templates for Zabbix 7.0 and 8.0."
