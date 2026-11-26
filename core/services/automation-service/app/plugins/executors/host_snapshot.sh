# SeyalRun host state snapshot: READ-ONLY, no root needed. bash -s -- [HEALTH_URL...]
# Compared before and after a patch by posture-service (app/rollout_core.py).
# Records are tab-separated and gzip+base64 wrapped, like posture_facts.sh.
export LC_MESSAGES=C
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; export PATH
_t() { s="$1"; shift; if command -v timeout >/dev/null 2>&1; then timeout "$s" "$@"; else "$@"; fi; }
_marker() { printf '%s.%s' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" "$(sed 's/.*) //' /proc/1/stat 2>/dev/null | cut -d' ' -f20)"; }
collect() {
  printf 'V\t1\n'
  printf 'B\t%s\n' "$(_marker)"
  printf 'K\t%s\n' "$(uname -r)"
  if [ -d /run/systemd/system ]; then
    printf 'I\tsystemd\n'
    _t 30 systemctl list-units --type=service --state=running --no-legend --plain --no-pager 2>/dev/null | awk '{print "S\t"$1}'
    _t 30 systemctl list-units --type=service --state=failed --no-legend --plain --no-pager 2>/dev/null | awk '{print "F\t"$1}'
    printf 'Y\t%s\n' "$(_t 10 systemctl is-system-running 2>/dev/null)"
  else
    printf 'I\tnone\n'
  fi
  if command -v ss >/dev/null 2>&1; then
    _t 20 ss -Hltn 2>/dev/null | awk '{print "L\t"$4}' | sort -u
  else
    # /proc/net/tcp*: state 0A is LISTEN; the port is the hex after the colon.
    cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | awk '$4 == "0A" {split($2, a, ":"); print a[length(a)]}' | sort -u |
      while read -r h; do printf 'L\t*:%d\n' "0x$h"; done
  fi
  awk '$3 !~ /^(proc|sysfs|tmpfs|devtmpfs|devpts|cgroup2?|mqueue|overlay|securityfs|debugfs|tracefs|pstore|bpf|configfs|fusectl|hugetlbfs|autofs|binfmt_misc|rpc_pipefs|nsfs|ramfs|efivarfs|selinuxfs|squashfs)$/ {print "D\t"$2}' /proc/mounts 2>/dev/null | sort -u
  for m in / /boot /var; do
    [ -d "$m" ] && df -Pk "$m" 2>/dev/null | awk -v m="$m" 'NR==2 {print "A\t"m"\t"$4}'
  done
  printf 'U\t%s\n' "$(who 2>/dev/null | wc -l | tr -d ' ')"
  if command -v dpkg >/dev/null 2>&1; then dpkg --audit 2>/dev/null | grep -q . && printf 'X\tdpkg has unfinished packages\n'; fi
  for u in "$@"; do
    if command -v curl >/dev/null 2>&1; then c="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -- "$u" 2>/dev/null)"
    elif command -v wget >/dev/null 2>&1; then c="$(wget -q -T 10 -t 1 -S -O /dev/null -- "$u" 2>&1 | awk '/HTTP\// {c=$2} END {print c}')"
    else c=none; fi
    printf 'H\t%s\t%s\n' "$u" "${c:-000}"
  done
  printf 'Z\tend\n'
}
if command -v gzip >/dev/null 2>&1; then
  echo "SEYALRUN-SNAP-BEGIN v1 gz"; collect "$@" | gzip -c | base64 -w 4000
else
  echo "SEYALRUN-SNAP-BEGIN v1 raw"; collect "$@" | base64 -w 4000
fi
echo "SEYALRUN-SNAP-END"
exit 0
