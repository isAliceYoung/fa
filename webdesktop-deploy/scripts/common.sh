#!/usr/bin/env bash
# Shared functions only: sourcing this file never installs or changes anything.
set -Eeuo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
log() { printf '[%s] %s\n' "$(date -u '+%F %T UTC')" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { (( EUID == 0 )) || die "Use sudo bash install.sh (no --user option is required)."; }

choose_runtime_user() {
  local requested="$1" saved="$2" invoking="$3" uid="$4" current="$5"
  if [[ -n "$requested" ]]; then printf '%s\n' "$requested"
  elif [[ -n "$saved" ]]; then printf '%s\n' "$saved"
  elif [[ -n "$invoking" && "$invoking" != root ]]; then printf '%s\n' "$invoking"
  elif [[ "$uid" == 0 ]]; then printf 'webdesktop\n'
  else printf '%s\n' "$current"
  fi
}

configure() {
  export WD_DEPLOY_MODE="${WD_DEPLOY_MODE:-auto}"
  case "$WD_DEPLOY_MODE" in auto|install|repair|service) ;; *) die "WD_DEPLOY_MODE must be auto, install, repair or service; in-place upgrades are not supported." ;; esac
  local mode="${1:-check}" saved_user="" saved_dir="" saved_node="" key value
  # Parse only known fields as data; never source a configuration file as code.
  if [[ -r /etc/webdesktop-deploy.conf ]]; then
    while IFS='=' read -r key value; do
      case "$key" in
        WD_USER) saved_user="$value" ;;
        WD_APP_DIR) saved_dir="$value" ;;
        WD_NODE_VERSION) saved_node="$value" ;;
      esac
    done < /etc/webdesktop-deploy.conf
  fi
  WD_USER="$(choose_runtime_user "${WD_USER:-}" "$saved_user" "${SUDO_USER:-}" "$EUID" "$(id -un)")"
  export WD_USER
  [[ "$WD_USER" != root && "$WD_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] || die "The application must run as a non-root Linux user."
  local record
  if ! record="$(getent passwd "$WD_USER")"; then
    [[ "$mode" == install && "$EUID" == 0 ]] || die "No installation user found. Run install.sh first, or specify an existing user."
    command -v useradd >/dev/null || die "useradd is required (Debian package: passwd)."
    log "Creating Linux account $WD_USER with a home directory and locked password (no sudo access added)."
    useradd --create-home --user-group --shell /bin/bash --password '!' "$WD_USER"
    record="$(getent passwd "$WD_USER")"
  fi
  [[ "$(id -u "$WD_USER")" != 0 ]] || die "The application user must not have UID 0."
  WD_HOME="$(cut -d: -f6 <<<"$record")"
  WD_GROUP="$(id -gn "$WD_USER")"
  [[ "$WD_GROUP" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] || die "Unsupported Linux group name."
  [[ "$WD_HOME" =~ ^/[A-Za-z0-9_./-]+$ && "$WD_HOME" != / ]] || die "Unsupported home directory."
  export WD_HOME WD_GROUP
  if [[ "$WD_USER" == "$saved_user" ]]; then
    export WD_APP_DIR="${WD_APP_DIR:-$saved_dir}"
    export WD_NODE_VERSION="${WD_NODE_VERSION:-$saved_node}"
  fi
  export WD_APP_DIR="${WD_APP_DIR:-$WD_HOME/webdesktop}"
  [[ "$WD_APP_DIR" =~ ^/[A-Za-z0-9_./-]+$ && "$WD_APP_DIR" != *'/../'* && "$WD_APP_DIR" != */.. ]] || die "Use an absolute installation path without spaces or '..'."
  [[ "$WD_APP_DIR" == "$WD_HOME/"* && "$WD_APP_DIR" != "$WD_HOME/" ]] || die "Application must be a subdirectory of $WD_HOME."
  export WD_REPO_URL="${WD_REPO_URL:-https://github.com/isAliceYoung/fa.git}"
  [[ "$WD_REPO_URL" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] || die "Use a GitHub HTTPS repository URL without a password or token."
  export WD_BRANCH="${WD_BRANCH:-main}"
  [[ "$WD_BRANCH" != -* && "$WD_BRANCH" =~ ^[A-Za-z0-9_./-]+$ ]] || die "Invalid branch name."
  export WD_SOURCE_COMMIT="${WD_SOURCE_COMMIT:-}"
  [[ -z "$WD_SOURCE_COMMIT" || "$WD_SOURCE_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || die "--commit must be a full 40-character Git commit SHA."
  export WD_SOURCE_COMMIT="${WD_SOURCE_COMMIT,,}"
  export WD_NODE_VERSION="${WD_NODE_VERSION:-22.23.2}"
  [[ "$WD_NODE_VERSION" =~ ^22\.[0-9]+\.[0-9]+$ ]] || die "WD_NODE_VERSION must be a full Node.js 22 version."
  export WD_SWAP_GB="${WD_SWAP_GB:-auto}"
  [[ "$WD_SWAP_GB" =~ ^(auto|0|1|2|4)$ ]] ||
  die "WD_SWAP_GB must be auto, 0, 1, 2 or 4."
  export WD_SKIP_CLAUDE="${WD_SKIP_CLAUDE:-0}"
  export PATH="/opt/nodejs/v$WD_NODE_VERSION/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
}

save_installation() {
  # No passwords here: this lets check.sh discover the selected user and path.
  local state
  state="$(mktemp /etc/webdesktop-deploy.conf.XXXXXX)"
  printf 'WD_USER=%s\nWD_APP_DIR=%s\nWD_NODE_VERSION=%s\n' "$WD_USER" "$WD_APP_DIR" "$WD_NODE_VERSION" > "$state"
  chmod 644 "$state"
  mv -f -- "$state" /etc/webdesktop-deploy.conf
}

require_debian() {
  [[ -r /etc/os-release ]] || die "Linux /etc/os-release is missing."
  local ID VERSION_ID
  source /etc/os-release
  [[ "$ID" == debian && "$VERSION_ID" == 12 ]] || die "This installer targets Debian 12."
  [[ -d /run/systemd/system ]] || die "A booted systemd system is required."
}

as_user() {
  if (( EUID == 0 )); then
    runuser -u "$WD_USER" -- env HOME="$WD_HOME" USER="$WD_USER" LOGNAME="$WD_USER" PATH="$PATH" "$@"
  else
    [[ "$(id -un)" == "$WD_USER" ]] || die "Run as $WD_USER or root."
    "$@"
  fi
}

apt_install() {
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends "$@"
}

backup_once() {
  [[ ! -e "$1" || -e "$1.webdesktop-original" ]] || cp -a -- "$1" "$1.webdesktop-original"
}

record_stage() {
  local module="$1" stage="$2" code="${3:-0}" state
  install -d -m 755 /var/lib/webdesktop-deploy || return 1
  state="$(mktemp /var/lib/webdesktop-deploy/status.XXXXXX)" || return 1
  if ! printf 'USER=%s\nAPP_DIR=%s\nMODULE=%s\nSTAGE=%s\nEXIT_CODE=%s\nUPDATED_AT=%s\n' \
    "$WD_USER" "$WD_APP_DIR" "$module" "$stage" "$code" "$(date -u +%FT%TZ)" > "$state"; then
    rm -f -- "$state"; return 1
  fi
  chmod 644 "$state" && mv -f -- "$state" /var/lib/webdesktop-deploy/status
}

require_complete_build() {
  [[ -f "$WD_APP_DIR/.next/BUILD_ID" ]] || die "Incomplete Next.js build: BUILD_ID is missing."
  [[ -f "$WD_APP_DIR/.next/required-server-files.json" ]] || die "Incomplete Next.js build: required-server-files.json is missing."
}

require_service_binding() {
  local installed_dir installed_user
  installed_dir="$(systemctl show webdesktop -p WorkingDirectory --value)"
  installed_user="$(systemctl show webdesktop -p User --value)"
  [[ "$installed_dir" == "$WD_APP_DIR" ]] || die "Existing webdesktop.service points to another application directory; no service was changed."
  [[ "$installed_user" == "$WD_USER" ]] || die "Existing webdesktop.service uses another Linux user; no service was changed."
}

service_diagnostics() {
  systemctl show webdesktop -p ActiveState -p SubState -p Result -p ExecMainStatus || true
  printf 'Inspect: sudo systemctl status webdesktop --no-pager\n' >&2
  printf 'Logs:    sudo journalctl -u webdesktop -n 80 --no-pager\n' >&2
}

wait_application_ready() {
  local attempt
  for attempt in {1..30}; do
    if systemctl is-active --quiet webdesktop && \
       curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 -o /dev/null http://127.0.0.1:3000/api/health && \
       as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" auth-state "$WD_APP_DIR" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  service_diagnostics
  return 1
}

install_service_unit() {
  local unit
  [[ ! -e /etc/systemd/system/webdesktop.service && ! -L /etc/systemd/system/webdesktop.service ]] || {
    printf 'Existing unit could not be read or appeared during recovery; refusing to overwrite it.\n' >&2
    return 1
  }
  unit="$(mktemp)"
  if ! python3 - "$DEPLOY_ROOT/templates/webdesktop.service" "$unit" <<'PY'
import os
import pathlib
import sys
text = pathlib.Path(sys.argv[1]).read_text()
for key in ('USER', 'GROUP', 'APP_DIR', 'HOME', 'NODE_VERSION'):
    text = text.replace('@' + key + '@', os.environ['WD_' + key])
with open(sys.argv[2], 'w', newline='\n') as handle:
    handle.write(text)
PY
  then rm -f -- "$unit"; return 1; fi
  if ! install -m 644 "$unit" /etc/systemd/system/webdesktop.service; then
    rm -f -- "$unit"; return 1
  fi
  rm -f -- "$unit"
  systemctl daemon-reload
}

check_local_port() {
  local listeners control_group pid pids
  if ! command -v ss >/dev/null; then
    # The minimal Debian image may not have iproute2 yet. Do not silently ignore a listener.
    local socket_files=(/proc/net/tcp)
    [[ ! -r /proc/net/tcp6 ]] || socket_files+=(/proc/net/tcp6)
    if awk '$2 ~ /:0BB8$/ && $4 == "0A" {found=1} END {exit !found}' "${socket_files[@]}"; then
      die "TCP port 3000 is already listening. Install iproute2 to inspect its owner; the installer will not kill it."
    else
      [[ "$?" == 1 ]] || die "Could not inspect listening sockets before iproute2 is installed."
    fi
    return 0
  fi
  listeners="$(ss -H -ltnp 'sport = :3000')" || die "Could not inspect TCP port 3000."
  [[ -n "$listeners" ]] || return 0
  systemctl cat webdesktop.service >/dev/null 2>&1 || die "Port 3000 belongs to an existing process, but no WebDesktop service is installed."
  require_service_binding
  systemctl is-active --quiet webdesktop || die "Port 3000 is occupied while WebDesktop is inactive; inspect it before installing."
  awk '$4 != "127.0.0.1:3000" {bad=1} END {exit bad}' <<<"$listeners" || die "Existing port 3000 listener is not limited to 127.0.0.1."
  control_group="$(systemctl show webdesktop -p ControlGroup --value)"
  [[ -n "$control_group" && "$control_group" != / ]] || die "Cannot identify the WebDesktop service cgroup."
  pids="$(grep -oE 'pid=[0-9]+' <<<"$listeners" | cut -d= -f2 | sort -u)" || die "Cannot identify the process listening on port 3000."
  [[ -n "$pids" ]] || die "Cannot identify the process listening on port 3000; run the installer through sudo."
  while read -r pid; do
    awk -F: -v group="$control_group" '$3 == group || index($3, group "/") == 1 {found=1} END {exit !found}' \
      "/proc/$pid/cgroup" || die "Port 3000 has a listener outside the WebDesktop service; no process was stopped."
  done <<<"$pids"
}

application_disk_path() {
  # Before the first clone, inspect the closest existing parent of the app path.
  local target="${WD_APP_DIR:-$WD_HOME}"
  while [[ ! -d "$target" && "$target" != / ]]; do
    target="$(dirname -- "$target")" || return 1
  done
  printf '%s\n' "$target"
}

# Resource status functions: 0 = OK, 2 = WARN, 1 = FAIL. They never change resources.
disk_capacity_status() {
  local target app_path free_mib status=0
  app_path="$(application_disk_path)" || return 1
  df -h -- / "$app_path" || return 1
  for target in / "$app_path"; do
    free_mib="$(df -Pm -- "$target" | awk 'NR==2 {print $4}')" || return 1
    [[ "$free_mib" =~ ^[0-9]+$ ]] || { printf 'Cannot detect free disk space on %s.\n' "$target"; return 1; }
    if (( free_mib == 0 )); then
      printf 'No free disk space on %s (0 MiB).\n' "$target"
      status=1
    elif (( free_mib < 2048 )); then
      printf 'Less than 2 GiB free disk space on %s: %s MiB.\n' "$target" "$free_mib"
      (( status == 1 )) || status=2
    fi
  done
  return "$status"
}

swap_capacity_status() {
  local areas ram_kib count
  swapon --show || return 1
  areas="$(swapon --show=NAME --noheadings)" || return 1
  count="$(awk 'NF {n++} END {print n+0}' <<<"$areas")" || return 1
  if (( count > 0 )); then
    printf 'Active Swap areas: %s.\n' "$count"
    return 0
  fi
  ram_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)" || return 1
  [[ "$ram_kib" =~ ^[0-9]+$ && "$ram_kib" -gt 0 ]] || { printf 'Cannot detect RAM size.\n'; return 1; }
  if (( ram_kib >= 8192 * 1024 )); then
    printf 'No active Swap; RAM is at least 8 GiB, so Swap is optional.\n'
    return 0
  fi
  printf 'No active Swap with %s MiB RAM; memory pressure can cause OOM, especially during a build.\n' "$((ram_kib / 1024))"
  return 2
}

preflight_resources() {
  local free_disk_mib target
  for target in / "$WD_HOME"; do
    free_disk_mib="$(df -Pm -- "$target" | awk 'NR==2 {print $4}')"
    [[ "$free_disk_mib" =~ ^[0-9]+$ ]] || die "Could not detect free space on $target."
    log "Available disk space on $target: ${free_disk_mib} MiB."
    if (( free_disk_mib < 8192 )); then
      log "WARNING: Less than 8 GiB free space on $target; packages, npm cache and the build may exhaust this filesystem."
    fi
  done
  check_local_port
}
