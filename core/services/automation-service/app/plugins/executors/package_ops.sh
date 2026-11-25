# SeyalRun package worker (fixed in the image; arguments validated by _host_ops.py).
# Run by host_op_launcher.sh as: bash run.sh DIR — see there for the contract.
dir="$1"; base="$(dirname "$dir")"
mkdir "$dir/started" 2>/dev/null || exit 0      # another launch of this token won
echo "$$" > "$dir/pid"
printf '%s.%s' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" "$(sed 's/.*) //' /proc/1/stat 2>/dev/null | cut -d' ' -f20)" > "$dir/marker"
exec >>"$dir/log" 2>&1 </dev/null
finish() { echo "$1" > "$dir/rc.tmp"; mv -f "$dir/rc.tmp" "$dir/rc"; exit "$1"; }
trap 'rc=$?; [ -f "$dir/rc" ] || { echo "${rc:-70}" > "$dir/rc"; }' EXIT

# LC_MESSAGES only: LC_ALL=C breaks yum 3 (Python 2) on non-ASCII metadata.
export LC_MESSAGES=C DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=l ZYPP_LOCK_TIMEOUT=300
unset LC_ALL
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
mapfile -t A < "$dir/args"
op="${A[0]}"; mode="${A[1]}"; items=("${A[@]:2}")
find "$base" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} + 2>/dev/null

if command -v flock >/dev/null 2>&1; then
  exec 9>"$base/.lock"
  flock -w 600 9 || { echo "another SeyalRun operation is still running on this host"; finish 73; }
fi

if command -v apt-get >/dev/null 2>&1 && command -v dpkg-query >/dev/null 2>&1; then pm=apt
elif command -v zypper >/dev/null 2>&1; then pm=zypper
elif command -v dnf >/dev/null 2>&1; then pm=dnf
elif command -v yum >/dev/null 2>&1; then pm=yum
else echo "no supported package manager (apt, dnf, yum, zypper)"; finish 69; fi
if [ "$op" != plan ] && command -v transactional-update >/dev/null 2>&1 && ! [ -w /usr ]; then
  echo "transactional (read-only) hosts are not supported"; finish 69
fi
# A guard that cannot run is not a guard: without these the checks below would pass
# on empty output.
for t in awk grep sed sort comm head tail base64 find; do
  command -v "$t" >/dev/null 2>&1 || { echo "required tool missing on this host: $t"; finish 69; }
done
echo "op=$op package-manager=$pm repos=$mode items=${#items[@]}"

# ── package manager options ──────────────────────────────────────────────────
APT=(apt-get -o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -o APT::Color=0 -o Dpkg::Use-Pty=0)
RPMBASE=("$pm" --setopt=clean_requirements_on_remove=0)
REPOOPT=()
ZYP=(zypper --non-interactive --no-color)
ZREPO=()
if [ "$mode" = managed ]; then
  # Only the mirrors SeyalRun wrote are visible to this operation. The host's own
  # repo files are left exactly as they are.
  case "$pm" in
    apt)
      mkdir -p "$dir/sources"
      cp /etc/apt/sources.list.d/seyalrun-*.sources "$dir/sources/" 2>/dev/null || { echo "no managed repo on this host; apply repos first"; finish 66; }
      APT+=(-o Dir::Etc::sourcelist=/dev/null -o "Dir::Etc::sourceparts=$dir/sources") ;;
    dnf|yum)
      ls /etc/yum.repos.d/seyalrun-*.repo >/dev/null 2>&1 || { echo "no managed repo on this host; apply repos first"; finish 66; }
      REPOOPT=('--disablerepo=*' '--enablerepo=seyalrun-*') ;;
    zypper)
      for f in /etc/zypp/repos.d/seyalrun-*.repo; do [ -e "$f" ] && ZREPO+=(--repo "$(basename "$f" .repo)"); done
      [ "${#ZREPO[@]}" -gt 0 ] || { echo "no managed repo on this host; apply repos first"; finish 66; } ;;
  esac
fi

RPM=("${RPMBASE[@]}" "${REPOOPT[@]}" -y)             # applies
RPMDRY=("${RPMBASE[@]}" "${REPOOPT[@]}" --assumeno)  # never applies
snap() {
  if [ "$pm" = apt ]; then dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\t${Architecture}\t${Version}\n' 2>/dev/null | awk -F'\t' '$1 ~ /^.i/ {print $2"\t"$3"\t"$4}'
  else rpm -qa --qf '%{NAME}\t%{ARCH}\t%|EPOCH?{%{EPOCH}:}:{}|%{VERSION}-%{RELEASE}\n' 2>/dev/null; fi | sort
}
payload() {
  # Changes as gzip+base64: run output keeps only its last few hundred lines.
  { printf 'V\t1\nM\t%s\n' "$pm"
    [ -f "$dir/skipped" ] && sed 's/^/H\t/' "$dir/skipped"
    if [ -f "$dir/before" ] && [ -f "$dir/after" ]; then
      comm -23 "$dir/before" "$dir/after" | sed 's/^/-\t/'
      comm -13 "$dir/before" "$dir/after" | sed 's/^/+\t/'
    fi
    [ -f "$dir/plan" ] && head -n 4000 "$dir/plan" | sed 's/^/P\t/'
    printf 'Z\tend\n'
  } > "$dir/payload"
  if command -v gzip >/dev/null 2>&1; then
    echo "SEYALRUN-PKG-BEGIN v1 gz"; gzip -c "$dir/payload" | base64 -w 4000
  else
    echo "SEYALRUN-PKG-BEGIN v1 raw"; base64 -w 4000 < "$dir/payload"
  fi
  echo "SEYALRUN-PKG-END"
}
refresh() {
  case "$pm" in
    apt)
      # apt-get update exits 0 when an index could not be fetched or its signature is
      # wrong and quietly keeps the old one. Its message is the only reliable signal
      # on every apt version this supports.
      "${APT[@]}" -qq update > "$dir/refresh" 2>&1; r=$?
      cat "$dir/refresh"
      if [ "$r" = 0 ] && grep -qE '^[WE]: .*(signature|[Ff]ailed to fetch|NO_PUBKEY|not signed|index files)|^Err:' "$dir/refresh"; then r=100; fi
      return "$r" ;;
    dnf|yum) "${RPM[@]}" -q makecache ;;            # -y: a repo key prompt would hang
    # No --gpg-auto-import-keys: trusting whatever key a repo offers is not this job's call.
    zypper) "${ZYP[@]}" refresh "${ZREPO[@]/--repo/-r}"; return $? ;;   # 106 = a repo was skipped: stale
  esac
}

# Items are name[:arch][=version]; turn them into what each manager expects.
spec() {
  local i="$1" v="" a=""
  case "$i" in *=*) v="${i#*=}"; i="${i%%=*}" ;; esac
  case "$i" in *:*) a="${i#*:}"; i="${i%%:*}" ;; esac
  case "$pm" in
    apt) printf '%s' "$i${a:+:$a}${v:+=$v}" ;;
    dnf|yum) printf '%s' "$i${v:+-$v}${a:+.$a}" ;;
    zypper) printf '%s' "$i${a:+.$a}${v:+=$v}" ;;
  esac
}
bare() { local i="${1%%=*}"; printf '%s' "${i%%:*}"; }
holds() {
  case "$pm" in
    apt) apt-mark showhold 2>/dev/null | sed 's/:.*//' ;;
    dnf|yum) "$pm" -q versionlock list 2>/dev/null | grep -v '^#' | sed -E 's/^[0-9]+://; s/-[0-9]+:.*//; s/-[^-]*-[^-]*$//' ;;
    zypper) zypper --non-interactive --no-color locks 2>/dev/null | awk -F'|' 'NR>2 {gsub(/ /,"",$2); if ($2 != "") print $2}' ;;
  esac
}
S=(); for i in "${items[@]}"; do [ "$op" = repo_apply ] || S+=("$(spec "$i")"); done

# Upgrades and plans leave held packages alone, by name, instead of failing or lying.
if [ "$op" = upgrade ] || [ "$op" = plan ] || [ "$op" = rollback ]; then
  holds | sort -u > "$dir/holds"
  if [ -s "$dir/holds" ] && [ "$op" != rollback ]; then
    keep=()
    for i in "${items[@]}"; do
      if grep -qxF -- "$(bare "$i")" "$dir/holds"; then echo "held, skipped: $(bare "$i")"; bare "$i" >> "$dir/skipped"; echo >> "$dir/skipped"
      else keep+=("$(spec "$i")"); fi
    done
    [ -f "$dir/skipped" ] && sed -i '/^$/d' "$dir/skipped"
    S=("${keep[@]}")
    if [ "${#S[@]}" -eq 0 ]; then echo "every requested package is held"; payload; finish 65; fi
  fi
fi

# Every name must be exactly one package. apt-get turns an unknown name containing a dot
# into a regular expression and a trailing + or - into the opposite action; dnf and zypper
# fall back to globs and "provides". All of them would act on more than was asked for.
installed_names() { if [ "$pm" = apt ]; then dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 2>/dev/null | awk '$1 ~ /^.i/ {print $2}'; else rpm -qa --qf '%{NAME}\n' 2>/dev/null; fi | sort -u; }
available() {
  case "$pm" in
    apt) apt-cache "${APT[@]:1}" pkgnames "$1" 2>/dev/null ;;
    dnf|yum) "$pm" "${REPOOPT[@]}" -q list "$1" 2>/dev/null | awk '{print $1}' | sed 's/\.[^.]*$//' ;;
    zypper) zypper --non-interactive --no-color --no-refresh search --match-exact -t package "$1" 2>/dev/null | awk -F'|' '{gsub(/ /,"",$2); print $2}' ;;
  esac | grep -qxF -- "$1"
}
exact() {       # exact installed|available
  local i n; installed_names > "$dir/installed"
  [ -s "$dir/installed" ] || { echo "the installed package list could not be read"; return 1; }
  for i in "${items[@]}"; do
    n="$(bare "$i")"
    grep -qxF -- "$n" "$dir/installed" && continue
    [ "$1" = available ] && available "$n" && continue
    echo "no package is named exactly '$n'"; return 1
  done
}
case "$op" in plan|upgrade|remove|purge|hold|unhold|rollback) exact installed || finish 66 ;; esac

zyp_rc() {
  # 102 reboot needed, 106 a repo was skipped: done. 103: zypper updated itself, go again.
  # 107: an RPM scriptlet failed — packages are on disk but the host may be broken.
  case "$1" in
    0|102|106) return 0 ;;
    7) echo "zypper is locked (PackageKit is the usual cause)"; return 73 ;;
    104) echo "a package was not found in the enabled repos"; return 66 ;;
    107) echo "an RPM script failed; run 'zypper verify' on this host"; return 1 ;;
    *) return "$1" ;;
  esac
}
zyp() {
  local pass=1 r
  while :; do
    "${ZYP[@]}" "$@"; r=$?
    if [ "$r" = 103 ] && [ "$pass" -lt 3 ]; then pass=$((pass + 1)); echo "zypper updated itself, running again ($pass/3)"; continue; fi
    zyp_rc "$r"; return $?
  done
}
PROTECTED_RX='apt|bash|coreutils|coreutils-single|dbus|dnf|dpkg|filesystem|glibc|grep|grub2-common|init|kernel|kernel-core|kernel-default|libc6|libzypp|login|microdnf|openssh|openssh-server|pam|passwd|rpm|shadow|shadow-utils|sudo|systemd|systemd-sysv|util-linux|yum|zypper'
guard_removal() {
  # Refuse when the solver would ALSO take a protected package with it. Every word of
  # the dry run is compared: zypper lists removals wrapped, several to a line. A false
  # match only refuses, which is the safe direction.
  local out hit r
  out="$("$@" 2>&1 </dev/null)"; r=$?
  case "$pm:$r" in
    apt:0|zypper:0|zypper:102|zypper:106|dnf:0|dnf:1|yum:0|yum:1) ;;
    *) printf '%s\n' "$out" | tail -n 15; echo "refused: the dry run failed (exit $r)"; return 1 ;;
  esac
  if [ "$pm" = dnf ] || [ "$pm" = yum ]; then
    if printf '%s\n' "$out" | grep -qE '^No [Mm]atch for argument|^Error'; then
      printf '%s\n' "$out" | tail -n 15; echo "refused: not installed, or the dry run reported an error"; return 1
    fi
  fi
  hit="$(printf '%s\n' "$out" | tr -s ' \t' '\n\n' | sed 's/:.*//' | sort -u | grep -xE "$PROTECTED_RX" | head -n 3 | tr '\n' ' ')"
  [ -z "$hit" ] || { echo "refused: this would also remove protected package(s): $hit"; return 1; }
  if printf '%s\n' "$out" | grep -qF -- "linux-image-$(uname -r)"; then echo "refused: this would remove the running kernel"; return 1; fi
}

snap > "$dir/before"
rc=0
case "$op" in
  refresh) refresh; rc=$? ;;

  plan)
    refresh || echo "warning: metadata refresh failed; planning from cached metadata"
    case "$pm" in
      apt) "${APT[@]}" -s install --only-upgrade --no-remove "${S[@]}" > "$dir/plan" 2>&1; rc=$? ;;
      dnf|yum)
        "${RPMDRY[@]}" upgrade "${S[@]}" > "$dir/plan" 2>&1; rc=$?
        # --assumeno declines its own transaction with exit 1; only a real error is one.
        if [ "$rc" = 1 ] && ! grep -qE '^(Error:|Error: )|^No match for argument|nothing provides|Failed to download metadata' "$dir/plan"; then rc=0; fi ;;
      zypper) "${ZYP[@]}" update --dry-run --auto-agree-with-licenses "${ZREPO[@]}" "${S[@]}" > "$dir/plan" 2>&1; zyp_rc $?; rc=$? ;;
    esac
    tail -n 60 "$dir/plan" ;;

  upgrade)
    if ! refresh; then
      echo "metadata refresh failed; not patching from stale metadata"; payload; finish 74
    fi
    case "$pm" in
      # --no-remove: an upgrade that needs to remove something is a decision for a person.
      apt) "${APT[@]}" -y install --only-upgrade --no-remove "${S[@]}"; rc=$? ;;
      dnf|yum) "${RPM[@]}" upgrade "${S[@]}"; rc=$? ;;
      zypper)
        # "update" ignores a version; a pinned name=version needs "install". Both by plain name.
        P=(); U=(); for i in "${S[@]}"; do case "$i" in *=*) P+=("$i") ;; *) U+=("$i") ;; esac; done
        [ "${#P[@]}" -eq 0 ] || { zyp install --name --auto-agree-with-licenses "${ZREPO[@]}" "${P[@]}"; rc=$?; }
        [ "$rc" != 0 ] || [ "${#U[@]}" -eq 0 ] || { zyp update --auto-agree-with-licenses "${ZREPO[@]}" "${U[@]}"; rc=$?; } ;;
    esac ;;

  install|pin)
    refresh || { echo "metadata refresh failed"; payload; finish 74; }
    exact available || finish 66
    case "$pm" in
      apt) x=(); [ "$op" = pin ] && x=(--allow-downgrades); "${APT[@]}" -y install --no-remove "${x[@]}" "${S[@]}"; rc=$? ;;
      dnf|yum)
        "${RPM[@]}" install "${S[@]}"; rc=$?
        if [ "$op" = pin ] && [ "$rc" = 0 ]; then
          for i in "${items[@]}"; do v="${i#*=}"; rpm -q --qf '%|EPOCH?{%{EPOCH}:}:{}|%{VERSION}-%{RELEASE}\n' "$(bare "$i")" | grep -qxF -- "$v" || { "${RPM[@]}" downgrade "$(spec "$i")" || rc=$?; }; done
        fi ;;
      zypper) x=(); [ "$op" = pin ] && x=(--oldpackage); zyp install --name --auto-agree-with-licenses "${x[@]}" "${ZREPO[@]}" "${S[@]}"; rc=$? ;;
    esac
    if [ "$op" = pin ] && [ "$rc" = 0 ]; then
      B=(); for i in "${items[@]}"; do B+=("$(bare "$i")"); done
      case "$pm" in
        apt) apt-mark hold "${B[@]}" || rc=$? ;;
        dnf|yum) "$pm" -y versionlock add "${B[@]}" || { echo "the versionlock plugin is not installed"; rc=69; } ;;
        zypper) "${ZYP[@]}" addlock "${B[@]}" || rc=$? ;;
      esac
    fi ;;

  remove|purge)
    case "$pm" in
      apt) guard_removal "${APT[@]}" -s "$op" "${S[@]}" || finish 65
           "${APT[@]}" -y "$op" "${S[@]}"; rc=$? ;;
      dnf|yum) guard_removal "${RPMDRY[@]}" remove "${S[@]}" || finish 65
           "${RPM[@]}" remove "${S[@]}"; rc=$? ;;
      zypper) guard_removal "${ZYP[@]}" remove --dry-run "${S[@]}" || finish 65
           zyp remove "${S[@]}"; rc=$? ;;
    esac ;;

  hold|unhold)
    case "$pm:$op" in
      apt:*) apt-mark "$op" "${S[@]}"; rc=$? ;;
      dnf:hold|yum:hold) "$pm" -y versionlock add "${S[@]}"; rc=$? ;;
      dnf:unhold|yum:unhold) "$pm" -y versionlock delete "${S[@]}"; rc=$? ;;
      zypper:hold) "${ZYP[@]}" addlock "${S[@]}"; rc=$? ;;
      zypper:unhold) "${ZYP[@]}" removelock "${S[@]}"; rc=$? ;;
    esac
    case "$pm" in dnf|yum) [ "$rc" = 0 ] || { echo "holding needs the dnf/yum versionlock plugin"; rc=69; } ;; esac ;;

  rollback)
    # Back to the recorded versions. Kernels install side by side: never downgraded here.
    K=(); for i in "${items[@]}"; do case "$(bare "$i")" in kernel|kernel-*|linux-image-*|linux-modules-*|linux-headers-*) echo "kernel left in place: $(bare "$i")"; bare "$i" >> "$dir/skipped"; echo >> "$dir/skipped" ;; *) K+=("$(spec "$i")") ;; esac; done
    [ -f "$dir/skipped" ] && sed -i '/^$/d' "$dir/skipped"
    if [ "${#K[@]}" -gt 0 ]; then
      refresh || echo "warning: metadata refresh failed"
      case "$pm" in
        apt) "${APT[@]}" -y install --allow-downgrades --no-remove "${K[@]}"; rc=$? ;;
        dnf|yum) "${RPM[@]}" downgrade "${K[@]}"; rc=$? ;;
        zypper) zyp install --name --oldpackage "${ZREPO[@]}" "${K[@]}"; rc=$? ;;
      esac
    fi ;;

  repo_apply)
    want="$dir/want"; : > "$want"
    case "$pm" in
      apt) rdir=/etc/apt/sources.list.d; kdir=/etc/apt/keyrings; fam=deb; pat='seyalrun-*.sources' ;;
      dnf|yum) rdir=/etc/yum.repos.d; kdir=/etc/pki/rpm-gpg; fam=rpm; pat='seyalrun-*.repo' ;;
      zypper) rdir=/etc/zypp/repos.d; kdir=/etc/zypp/seyalrun-keys; fam=zypp; pat='seyalrun-*.repo' ;;
    esac
    mkdir -p "$rdir" "$kdir"
    for i in "${items[@]}"; do
      t="${i%%:*}"; rest="${i#*:}"; f="${rest%%:*}"; b="${rest#*:}"
      case "$f" in seyalrun-*) ;; *) echo "bad file name"; finish 64 ;; esac
      case "$f" in */*) echo "bad file name"; finish 64 ;; esac
      if [ "$t" = key ]; then d="$kdir"; elif [ "$t" = "$fam" ]; then d="$rdir"
      else echo "repo family $t does not fit this host ($fam)"; finish 65; fi
      printf '%s' "$b" | base64 -d > "$d/.$f.tmp" && chmod 644 "$d/.$f.tmp" && mv -f "$d/.$f.tmp" "$d/$f" || finish 73
      [ "$t" = key ] && [ "$pm" != apt ] && { rpm --import "$d/$f" || finish 65; }
      echo "$d/$f" >> "$want"
    done
    chmod 755 "$kdir"
    for f in "$rdir"/$pat "$kdir"/seyalrun-*.asc; do
      [ -e "$f" ] && ! grep -qxF -- "$f" "$want" && { echo "removing $f"; rm -f -- "$f"; }
    done
    if [ "${#items[@]}" -gt 0 ]; then
      mode=managed
      case "$pm" in
        apt) mkdir -p "$dir/sources"; cp "$rdir"/seyalrun-*.sources "$dir/sources/"; APT+=(-o Dir::Etc::sourcelist=/dev/null -o "Dir::Etc::sourceparts=$dir/sources") ;;
        dnf|yum) RPM+=('--disablerepo=*' '--enablerepo=seyalrun-*') ;;
        zypper) for f in "$rdir"/seyalrun-*.repo; do ZREPO+=(--repo "$(basename "$f" .repo)"); done ;;
      esac
      refresh || { echo "the repos were written but could not be read"; rc=74; }
    fi ;;
  *) echo "unknown op"; finish 64 ;;
esac

snap > "$dir/after"
if [ "$pm" = apt ] && [ "$op" != plan ]; then
  a="$(dpkg --audit 2>/dev/null | head -5)"
  [ -z "$a" ] || { echo "dpkg reports unfinished packages:"; echo "$a"; [ "$rc" = 0 ] && rc=1; }
fi
payload
echo "exit=$rc"
finish "$rc"
