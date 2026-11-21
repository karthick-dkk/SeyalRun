# shellcheck shell=bash
# Shared by deploy-staging.sh, deploy-staging-images.sh and verify-staging.sh.
# Sourced, not executed. Expects $HOST to be set by the caller.
#
# Environment:
#   SSH_USER       - remote SSH user (default: test)
#   REMOTE_DIR     - where the stack lives on the host (default: /opt/seyalrun-v2).
#                    Keep the basename "seyalrun-v2": it is the compose project
#                    name, and the db overlay re-adopts seyalrun-v2-postgres-1.
#   SUDO_PASSWORD  - only needed when the SSH user is NOT in the docker group,
#                    or REMOTE_DIR does not exist yet and its parent is root-owned.
#                    Sent over SSH stdin to `sudo -S`, never on a command line.

SSH_USER="${SSH_USER:-test}"
REMOTE_DIR="${REMOTE_DIR:-/opt/seyalrun-v2}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

rssh() { ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "$@"; }

# Can the SSH user drive docker directly? Probed once per script run.
_STAGING_DOCKER_DIRECT=""
_docker_direct() {
  if [[ -z "$_STAGING_DOCKER_DIRECT" ]]; then
    if rssh 'docker info >/dev/null 2>&1'; then _STAGING_DOCKER_DIRECT=yes; else _STAGING_DOCKER_DIRECT=no; fi
  fi
  [[ "$_STAGING_DOCKER_DIRECT" == yes ]]
}

# remote_docker CMD — runs CMD on the host with docker rights: as the SSH user
# when it is in the docker group, otherwise as root through sudo. With
# STAGING_NONINTERACTIVE=1 it never prompts (sudo -n) and fails instead.
remote_docker() {
  local cmd; cmd="bash -c $(printf '%q' "$1")"
  if _docker_direct; then
    rssh "$cmd"
  elif [[ -n "${SUDO_PASSWORD:-}" ]]; then
    rssh "sudo -S $cmd" <<< "$SUDO_PASSWORD"
  elif [[ "${STAGING_NONINTERACTIVE:-0}" == 1 ]]; then
    rssh "sudo -n $cmd"
  else
    rssh -tt "sudo $cmd"
  fi
}

# ensure_remote_dir — REMOTE_DIR exists and the SSH user can write to it.
# Uses sudo only when the user cannot create it themselves.
ensure_remote_dir() {
  if rssh "mkdir -p '${REMOTE_DIR}' 2>/dev/null && test -w '${REMOTE_DIR}'"; then
    return 0
  fi
  local cmd="mkdir -p '${REMOTE_DIR}' && chown ${SSH_USER}:${SSH_USER} '${REMOTE_DIR}'"
  if [[ -n "${SUDO_PASSWORD:-}" ]]; then
    rssh "sudo -S bash -c \"$cmd\"" <<< "$SUDO_PASSWORD"
  else
    rssh -tt "sudo bash -c \"$cmd\""
  fi
}
