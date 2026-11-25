# SeyalRun reboot worker. Run by host_op_launcher.sh. The exit code is recorded BEFORE
# the reboot is requested, so re-attaching with the same token can never reboot twice.
dir="$1"
mkdir "$dir/started" 2>/dev/null || exit 0
echo "$$" > "$dir/pid"
m="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null).$(sed 's/.*) //' /proc/1/stat 2>/dev/null | cut -d' ' -f20)"
printf '%s' "$m" > "$dir/marker"
exec >>"$dir/log" 2>&1 </dev/null
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
fail() { echo "$1"; echo "$2" > "$dir/rc"; exit "$2"; }
echo "SEYALRUN-MARKER $m"
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then how=(systemctl reboot)
elif command -v shutdown >/dev/null 2>&1; then how=(shutdown -r now)
elif command -v reboot >/dev/null 2>&1; then how=(reboot)
else fail "no way to reboot this host (no systemctl, shutdown or reboot)" 69; fi
base="$(dirname "$dir")"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$base/.lock"
  flock -w 30 9 || fail "a package operation is still running; not rebooting" 73
fi
for p in apt-get dpkg dnf yum rpm zypper; do
  pgrep -x "$p" >/dev/null 2>&1 && fail "$p is running; not rebooting" 73
done
sync
echo "reboot requested"
echo 0 > "$dir/rc.tmp"; mv -f "$dir/rc.tmp" "$dir/rc"; sync
sleep 6     # let the launcher report before the connection goes away
"${how[@]}" || echo "reboot command failed with $?"
