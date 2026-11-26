# SeyalRun host-operation launcher. Sent on stdin by the package_ops / host_reboot
# executors: bash -s -- TOKEN LINE_OFFSET WORKER_ARGS...
#
# The worker runs DETACHED from this SSH session. A dropped connection, a restarted
# sshd or a restarted automation-service must never interrupt dpkg/rpm half way, and
# must never run the same operation twice: TOKEN names the operation, and a second
# launch with the same TOKEN only re-attaches to the first one's log and exit code.
export LC_MESSAGES=C
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; export PATH
umask 077
token="$1"; offset="$2"; shift 2
case "$token" in ''|*[!A-Za-z0-9-]*) echo "bad token"; exit 64 ;; esac
case "$offset" in ''|*[!0-9]*) echo "bad offset"; exit 64 ;; esac
[ "$(id -u)" = 0 ] || { echo "root is required (connected as $(id -un), sudo did not give root)"; exit 77; }

base=/var/lib/seyalrun/ops; dir="$base/$token"
mkdir -p "$base" && chmod 700 /var/lib/seyalrun "$base" || { echo "cannot create $base"; exit 73; }
if [ -L /var/lib/seyalrun ] || [ -L "$base" ] || [ "$(stat -c %u "$base")" != 0 ]; then echo "$base is not a root-owned directory"; exit 73; fi
_marker() { printf '%s.%s' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" "$(sed 's/.*) //' /proc/1/stat 2>/dev/null | cut -d' ' -f20)"; }

mkdir -p "$dir" || exit 73
# "started" is created by the worker itself, atomically. Without it nothing ran yet,
# so launching (again) is safe; two launches race for it and one loses.
if [ ! -d "$dir/started" ]; then
  cat > "$dir/run.sh.$$" <<'SEYALRUN_WORKER_EOF'
@@WORKER@@
SEYALRUN_WORKER_EOF
  mv -f "$dir/run.sh.$$" "$dir/run.sh"
  : > "$dir/args.$$"; for a in "$@"; do printf '%s\n' "$a" >> "$dir/args.$$"; done
  mv -f "$dir/args.$$" "$dir/args"
  [ -f "$dir/log" ] || : > "$dir/log"
  if [ -d /run/systemd/system ] && command -v systemd-run >/dev/null 2>&1 &&
     systemd-run --quiet --collect --unit="seyalrun-op-$token" /bin/bash "$dir/run.sh" "$dir" 2>/dev/null; then :
  elif command -v setsid >/dev/null 2>&1; then
    setsid nohup bash "$dir/run.sh" "$dir" </dev/null >/dev/null 2>&1 &
  else
    nohup bash "$dir/run.sh" "$dir" </dev/null >/dev/null 2>&1 &
  fi
  i=0; while [ ! -d "$dir/started" ] && [ "$i" -lt 20 ]; do sleep 0.5; i=$((i + 1)); done
  [ -d "$dir/started" ] || { echo "the operation did not start"; exit 71; }
fi

_alive() {
  [ "$(cat "$dir/marker" 2>/dev/null)" = "$(_marker)" ] || return 1
  p="$(cat "$dir/pid" 2>/dev/null)"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}
# Everything after this line comes from the worker's log: the offset counts only those.
echo "SEYALRUN-LOG"
n="$offset"; dead=0
while :; do
  fin=0; [ -f "$dir/rc" ] && fin=1
  total="$(wc -l < "$dir/log" 2>/dev/null || echo 0)"; total=$((total + 0))
  if [ "$total" -gt "$n" ]; then
    tail -n "+$((n + 1))" "$dir/log" | head -n "$((total - n))"; n="$total"
  fi
  [ "$fin" = 1 ] && break
  if _alive; then dead=0; else dead=$((dead + 1)); fi
  if [ "$dead" -ge 3 ]; then echo "SEYALRUN-OP-INTERRUPTED the operation stopped without finishing"; exit 75; fi
  sleep 2
done
rc="$(cat "$dir/rc")"; case "$rc" in ''|*[!0-9]*) rc=70 ;; esac
exit "$rc"
