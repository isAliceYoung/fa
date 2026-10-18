#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

usage() {
  cat <<'EOF'
Usage: sudo bash install.sh [options]
  --user USER          Optional Linux runtime user (auto-detected; created if absent)
  --install-dir PATH   Application directory (default: USER's home/webdesktop)
  --repo URL           Source repository (default: https://github.com/isAliceYoung/fa.git)
  --branch BRANCH      Source branch for a new clone (default: main)
  --swap-gb 0|2        Add 2 GiB swap only if RAM < 4 GiB and no swap (default: 2)
  --skip-claude        Skip Claude Desktop installation
  --help              Show this help
No proxy, domain, reverse proxy or public firewall configuration is installed.
Existing source, databases, .env and input method configuration are preserved.
With sudo: use the invoking user. Direct root login: create/use 'webdesktop'.
On a later run: reuse the recorded installation user and path automatically.
EOF
}

while (( $# )); do
  case "$1" in
    --user|--install-dir|--repo|--branch|--swap-gb)
      (( $# >= 2 )) || { echo "Missing value for $1" >&2; exit 64; }
      case "$1" in
        --user) export WD_USER="$2" ;;
        --install-dir) export WD_APP_DIR="$2" ;;
        --repo) export WD_REPO_URL="$2" ;;
        --branch) export WD_BRANCH="$2" ;;
        --swap-gb) export WD_SWAP_GB="$2" ;;
      esac
      shift 2 ;;
    --skip-claude) export WD_SKIP_CLAUDE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 64 ;;
  esac
done

source "$ROOT/scripts/common.sh"
require_root
require_debian
command -v flock >/dev/null || die "flock is required (Debian package: util-linux)."
exec 9>/run/lock/webdesktop-install.lock
flock -n 9 || die "Another WebDesktop installer is running."

install -d -m 700 /var/log/webdesktop-install
LOG_FILE="/var/log/webdesktop-install/$(date -u +%Y%m%dT%H%M%S)-$$.log"
umask 077
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'printf "Installation failed at line %s; log: %s\n" "$LINENO" "$LOG_FILE" >&2' ERR

configure install
save_installation
# Copy helpers out of potentially root-only directories before using as_user.
TOOL_ROOT=/usr/local/lib/webdesktop-deploy
[[ ! -L "$TOOL_ROOT" ]] || die "$TOOL_ROOT must not be a symbolic link."
if [[ "$ROOT" != "$TOOL_ROOT" ]]; then
  install -d -m 755 "$TOOL_ROOT" "$TOOL_ROOT/scripts" "$TOOL_ROOT/patches" "$TOOL_ROOT/templates"
  for file in "$ROOT/"*.sh "$ROOT/scripts/"*.sh "$ROOT/scripts/"*.cjs "$ROOT/patches/"*.py "$ROOT/templates/"*.service "$ROOT/README.md"; do
    relative="${file#"$ROOT/"}"
    install -m 644 "$file" "$TOOL_ROOT/$relative"
  done
fi

log "User: $WD_USER; application: $WD_APP_DIR; local port: 3000"
for module in install-system install-nodejs install-postgres install-gui install-claude install-fcitx5 install-webdesktop; do
  log "Running $module"
  bash "$TOOL_ROOT/scripts/$module.sh"
done
log "Installation finished. Check status: sudo bash $TOOL_ROOT/check.sh"
printf '\nInitial login seeds are in %s/.env (readable only by its owner).\n' "$WD_APP_DIR"
printf "View them on the VPS: sudo -u %s grep -E '^(ADMIN_USER|ADMIN_PASSWORD|ADMIN_PATH|DESKTOP_PASSWORD)=' %s/.env\n" "$WD_USER" "$WD_APP_DIR"
printf 'These seeds are not the current passwords after a password change in the web interface.\n'
printf 'Server-local address: http://127.0.0.1:3000\n'
