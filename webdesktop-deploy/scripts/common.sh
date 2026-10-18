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
  export WD_NODE_VERSION="${WD_NODE_VERSION:-22.23.2}"
  [[ "$WD_NODE_VERSION" =~ ^22\.[0-9]+\.[0-9]+$ ]] || die "WD_NODE_VERSION must be a full Node.js 22 version."
  export WD_SWAP_GB="${WD_SWAP_GB:-2}"
  [[ "$WD_SWAP_GB" == 0 || "$WD_SWAP_GB" == 2 ]] || die "WD_SWAP_GB must be 0 or 2."
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
