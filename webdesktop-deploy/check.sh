#!/usr/bin/env bash
# Read-only checks. No install, restart, migration or password output.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
while (( $# )); do
  case "$1" in
    --user|--install-dir)
      (( $# >= 2 )) || { echo "Missing value for $1" >&2; exit 64; }
      if [[ "$1" == --user ]]; then export WD_USER="$2"; else export WD_APP_DIR="$2"; fi
      shift 2 ;;
    --help|-h)
      echo 'Usage: sudo bash check.sh [--user USER] [--install-dir PATH]';
      echo 'User and installation path are auto-detected from the saved installation.'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 64 ;;
  esac
done
source "$ROOT/scripts/common.sh"
configure
helper="$ROOT/scripts/bootstrap.cjs"
if [[ -r /usr/local/lib/webdesktop-deploy/scripts/bootstrap.cjs ]]; then
  helper=/usr/local/lib/webdesktop-deploy/scripts/bootstrap.cjs
fi
failures=0
check() {
  local name="$1"; shift
  if "$@"; then printf 'OK   %s\n' "$name"; else printf 'FAIL %s\n' "$name"; failures=$((failures + 1)); fi
}
has_bin() { command -v "$1" >/dev/null; }
listener_local() {
  local listeners
  listeners="$(ss -H -ltn 'sport = :3000')"
  [[ -n "$listeners" ]] || return 1
  awk '$4 != "127.0.0.1:3000" {bad=1} END {exit bad}' <<<"$listeners"
}
env_permissions() { [[ "$(stat -c '%a:%U' "$WD_APP_DIR/.env")" == "600:$WD_USER" ]]; }
printf 'System: '; . /etc/os-release; printf '%s\n' "$PRETTY_NAME"
printf 'Application: %s\n' "$WD_APP_DIR"
printf 'User: %s\n' "$WD_USER"
free -h
swapon --show
check 'Application source' test -f "$WD_APP_DIR/package.json"
check 'Production build' test -f "$WD_APP_DIR/.next/BUILD_ID"
check 'noVNC assets' test -f "$WD_APP_DIR/public/novnc/core/rfb.js"
for bin in node npm Xvnc openbox dbus-run-session chromium fcitx5; do check "Executable: $bin" has_bin "$bin"; done
if command -v node >/dev/null; then node --version; fi
for package in postgresql tigervnc-standalone-server chromium fcitx5 claude-desktop; do
  dpkg-query -W -f='${Package} ${Version} ${db:Status-Status}\n' "$package" 2>/dev/null || true
done
check 'PostgreSQL service' systemctl is-active --quiet postgresql
check 'WebDesktop service' systemctl is-active --quiet webdesktop
check 'Local-only TCP listener on 127.0.0.1:3000' listener_local
check 'Local HTTP endpoint' curl --noproxy '*' -fsS -o /dev/null --max-time 10 http://127.0.0.1:3000/
check '.env ownership and permission 600' env_permissions
check 'Required configuration (values hidden)' as_user node "$helper" check-env "$WD_APP_DIR"
check 'Database connection (credentials hidden)' as_user node "$helper" check-db "$WD_APP_DIR"
check 'Administrator and desktop login records' as_user node "$helper" auth-state "$WD_APP_DIR"
check 'Fcitx5 profile' test -f "$WD_HOME/.config/fcitx5/profile"
printf '\nFailed checks: %s\n' "$failures"
(( failures == 0 ))
