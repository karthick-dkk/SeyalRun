# SeyalRun posture facts collector, payload format v1.
#
# READ-ONLY. It queries the package database and the package manager's existing
# metadata; it never installs, removes, refreshes or reconfigures anything, and
# it needs no root. Run by automation-service's posture_facts executor over the
# same SSH path (and vault credential) as every other job; the output is parsed
# by posture-service (app/facts.py), which is the other half of this contract.
#
# Record format (one per line, tab-separated, first field is the record type):
#   V version | H hostname | K kernel | A arch | R os-release line | L legacy release
#   M package manager | I installed pkg | P apt upgradable line | S apt-get sim line
#   C dnf/yum check-update line | Q security advisory line | N installed kernel
#   W zypper list-updates row
#   B reboot flag + method | E warning | Z end-of-payload sentinel
#
# The records are gzip-compressed and base64-wrapped between the BEGIN/END
# markers: job output keeps only the last few hundred lines, and a host with
# 2,000 packages would otherwise blow straight through that.

export LC_ALL=C LANG=C
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

_t() {
  # _t SECONDS cmd... — bound a slow package-manager call when timeout(1) exists.
  _secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$_secs" "$@"; else "$@"; fi
}

_prefix() {
  # _prefix TYPE — prefix every stdin line with "TYPE<tab>", skipping blanks.
  while IFS= read -r _l || [ -n "$_l" ]; do
    [ -n "$_l" ] && printf '%s\t%s\n' "$1" "$_l"
  done
}

collect() {
  printf 'V\t1\n'
  printf 'H\t%s\n' "$(hostname 2>/dev/null || cat /proc/sys/kernel/hostname 2>/dev/null)"
  printf 'K\t%s\n' "$(uname -r 2>/dev/null)"
  printf 'A\t%s\n' "$(uname -m 2>/dev/null)"
  if [ -r /etc/os-release ]; then
    _prefix R < /etc/os-release
  elif [ -r /usr/lib/os-release ]; then
    _prefix R < /usr/lib/os-release
  fi
  [ -r /etc/redhat-release ] && head -n1 /etc/redhat-release | _prefix L

  if command -v dpkg-query >/dev/null 2>&1; then
    printf 'M\tdpkg\n'
    # Source package + version too: Debian/Ubuntu advisories name source
    # packages (openssl), hosts install binaries (libssl3).
    dpkg-query -W -f='I\t${Package}\t${Version}\t${Architecture}\t${Status}\t${source:Package}\t${source:Version}\n' 2>/dev/null
    # Pending updates from the host's EXISTING apt lists — no `apt-get update`
    # (that needs root and would fetch). Freshness is whatever the host last fetched.
    if command -v apt >/dev/null 2>&1; then
      _t 120 apt list --upgradable 2>/dev/null | grep -v '^Listing' | _prefix P
    elif command -v apt-get >/dev/null 2>&1; then
      _t 120 apt-get -s -o Debug::NoLocking=1 dist-upgrade 2>/dev/null | grep '^Inst ' | _prefix S
    else
      printf 'E\tno apt front-end found: pending updates unknown\n'
    fi
    if [ -f /var/run/reboot-required ] || [ -f /run/reboot-required ]; then
      printf 'B\t1\treboot-required-file\n'
    elif [ -d /var/lib/update-notifier ] || command -v unattended-upgrade >/dev/null 2>&1; then
      # These maintain the flag file, so its absence is meaningful.
      printf 'B\t0\treboot-required-file\n'
    fi
  elif command -v rpm >/dev/null 2>&1; then
    printf 'M\trpm\n'
    rpm -qa --qf 'I\t%{NAME}\t%|EPOCH?{%{EPOCH}:}:{}|%{VERSION}-%{RELEASE}\t%{ARCH}\tinstalled\n' 2>/dev/null
    rpm -q --qf 'N\t%{NAME}\t%{VERSION}-%{RELEASE}.%{ARCH}\t%{INSTALLTIME}\n' kernel kernel-core 2>/dev/null | grep '^N'
    _pm=""
    if command -v dnf >/dev/null 2>&1; then _pm=dnf; elif command -v yum >/dev/null 2>&1; then _pm=yum; fi
    if command -v zypper >/dev/null 2>&1; then
      # --no-refresh: whatever metadata the host last fetched, as for apt.
      _out="$(_t 180 zypper --non-interactive --no-refresh list-updates 2>/dev/null </dev/null)"; _rc=$?
      if [ "$_rc" -eq 0 ]; then
        printf '%s\n' "$_out" | grep '^v ' | _prefix W
        printf 'E\tzypper does not say which package updates are security updates\n'
      else
        printf 'E\tzypper list-updates failed (exit %s): pending updates unknown\n' "$_rc"
      fi
      _t 60 zypper --non-interactive needs-rebooting >/dev/null 2>&1 </dev/null; _rc=$?
      case "$_rc" in
        0) printf 'B\t0\tzypper\n' ;;
        102) printf 'B\t1\tzypper\n' ;;
      esac
    elif [ -n "$_pm" ]; then
      # check-update exits 100 when updates exist, 0 when none, 1 on error.
      _out="$(_t 180 "$_pm" -q check-update 2>/dev/null)"; _rc=$?
      if [ "$_rc" -eq 0 ] || [ "$_rc" -eq 100 ]; then
        printf '%s\n' "$_out" | _prefix C
      else
        printf 'E\t%s check-update failed (exit %s): pending updates unknown\n' "$_pm" "$_rc"
      fi
      if [ "$_pm" = dnf ]; then
        _t 180 dnf -q updateinfo list --security 2>/dev/null | _prefix Q
      else
        _t 180 yum -q updateinfo list security 2>/dev/null | _prefix Q
      fi
    else
      printf 'E\tno dnf/yum found: pending updates unknown\n'
    fi
    if command -v needs-restarting >/dev/null 2>&1; then
      _t 60 needs-restarting -r >/dev/null 2>&1; _rc=$?
      case "$_rc" in
        0) printf 'B\t0\tneeds-restarting\n' ;;
        1) printf 'B\t1\tneeds-restarting\n' ;;
        *) printf 'E\tneeds-restarting exited %s\n' "$_rc" ;;
      esac
    fi
  else
    printf 'E\tunsupported package manager (neither dpkg nor rpm found)\n'
  fi
  printf 'Z\tend\n'
}

if command -v gzip >/dev/null 2>&1; then _enc=gz; else _enc=raw; fi
echo "SEYALRUN-FACTS-BEGIN v1 ${_enc}"
if [ "$_enc" = gz ]; then
  collect | gzip -c | { base64 -w 4000 2>/dev/null || base64; }
else
  collect | { base64 -w 4000 2>/dev/null || base64; }
fi
echo "SEYALRUN-FACTS-END"
exit 0
