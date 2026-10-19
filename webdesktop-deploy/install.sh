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
  --commit SHA         Optional full source commit for a new clone (existing source must match)
  --swap-gb auto|0|1|2|4  Desired total active swap in GiB (default: auto)
  --skip-claude        Skip Claude Desktop installation
  --mode MODE         auto (default), install (first installation), repair (preserve app)
                      service (recover existing service only; no package installation)
  --help              Show this help
No proxy, domain, reverse proxy or public firewall configuration is installed.
Existing source, databases, .env and input method configuration are preserved.
With sudo: use the invoking user. Direct root login: create/use 'webdesktop'.
On a later run: reuse the recorded installation user and path automatically.
EOF
}

while (( $# )); do
  case "$1" in
    --user|--install-dir|--repo|--branch|--commit|--swap-gb|--mode)
      (( $# >= 2 )) || { echo "Missing value for $1" >&2; exit 64; }
      case "$1" in
        --user) export WD_USER="$2" ;;
        --install-dir) export WD_APP_DIR="$2" ;;
        --repo) export WD_REPO_URL="$2" ;;
        --branch) export WD_BRANCH="$2" ;;
        --commit) export WD_SOURCE_COMMIT="$2" ;;
        --swap-gb) export WD_SWAP_GB="$2" ;;
        --mode) export WD_DEPLOY_MODE="$2" ;;
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
export LOG_FILE="/var/log/webdesktop-install/$(date -u +%Y%m%dT%H%M%S)-$$.log"
umask 077
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'printf "Installation failed at line %s; log: %s\n" "$LINENO" "$LOG_FILE" >&2' ERR

case "${WD_DEPLOY_MODE:-auto}" in
  repair|service) configure; require_complete_build ;;
  *) configure install ;;
esac
save_installation
preflight_resources
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
bash "$TOOL_ROOT/scripts/run-modules.sh"
log "Installation finished. Check status: sudo bash $TOOL_ROOT/check.sh"
printf '\nInitial login seeds are in %s/.env (readable only by its owner).\n' "$WD_APP_DIR"
printf 'Credentials are not shown by install.sh or check.sh; inspect the protected .env locally when needed.\n'
printf 'These seeds are not the current passwords after a password change in the web interface.\n'
printf 'Server-local address: http://127.0.0.1:3000\n'
