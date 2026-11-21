#!/bin/bash
# A tag must match the version the repo tells people to install. Bumping the
# pins was a manual "remember to" step in the release loop; this makes a
# forgotten bump fail the release before any image is pushed.
#
#   bash .github/scripts/check-release-version.sh 2.0.2
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
v="${1:?usage: $0 <version, without the leading v>}"
fail=0
need() {  # need FILE FIXED-STRING
  if grep -qF -- "$2" "$1"; then echo "ok: $1 has '$2'"; else echo "::error file=$1::expected '$2' for release $v" >&2; fail=1; fi
}
need .env.example "SEYALRUN_VERSION=${v}"
# Pre-releases (2.1.0-rc1) are not advertised in the README or installer.
if [[ "$v" != *-* ]]; then
  need README.md   "Current release: **v${v}**"
  need README.md   "SEYALRUN_VERSION=${v} curl"
  need install.sh  "SEYALRUN_VERSION=${v}"
fi
exit "$fail"
