#!/usr/bin/env bash
# Read-only checks. No install, restart, migration or password output.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
gui_smoke=0
while (( $# )); do
  case "$1" in
    --gui-smoke) gui_smoke=1; shift ;;
    --user|--install-dir)
      (( $# >= 2 )) || { echo "Missing value for $1" >&2; exit 64; }
      if [[ "$1" == --user ]]; then export WD_USER="$2"; else export WD_APP_DIR="$2"; fi
      shift 2 ;;
    --help|-h)
      echo 'Usage: sudo bash check.sh [--user USER] [--install-dir PATH] [--gui-smoke]';
      echo '--gui-smoke starts a temporary GUI test session; default checks are read-only.';
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
tool_root="${helper%/scripts/bootstrap.cjs}"
failures=0
check() {
  local name="$1"; shift
  if "$@"; then printf 'OK   %s\n' "$name"; else printf 'FAIL %s\n' "$name"; failures=$((failures + 1)); fi
}
resource_check() {
  local name="$1" code; shift
  if "$@"; then
    printf 'OK   %s\n' "$name"
  else
    code=$?
    if [[ "$code" == 2 ]]; then
      printf 'WARN %s\n' "$name"
    else
      printf 'FAIL %s\n' "$name"
      failures=$((failures + 1))
    fi
  fi
}
has_bin() { command -v "$1" >/dev/null; }
listener_local() {
  local listeners
  listeners="$(ss -H -ltn 'sport = :3000')"
  [[ -n "$listeners" ]] || return 1
  awk '$4 != "127.0.0.1:3000" {bad=1} END {exit bad}' <<<"$listeners"
}
env_permissions() { [[ "$(stat -c '%a:%U' "$WD_APP_DIR/.env")" == "600:$WD_USER" ]]; }
service_binding() {
  [[ "$(systemctl show webdesktop -p WorkingDirectory --value)" == "$WD_APP_DIR" && \
     "$(systemctl show webdesktop -p User --value)" == "$WD_USER" ]]
}
ipv4_only_status() {
  local scope path addresses routes

  # Kernel has no IPv6 support.
  [[ -e /proc/sys/net/ipv6/conf/all/disable_ipv6 ]] ||
    return 0

  for scope in all default lo; do
    path="/proc/sys/net/ipv6/conf/$scope/disable_ipv6"

    [[ -r "$path" && "$(<"$path")" == 1 ]] ||
      return 1
  done

  addresses="$(ip -6 -o addr show)" || return 1
  routes="$(ip -6 route show)" || return 1

  [[ -z "$addresses" && -z "$routes" ]]
}

unit_present() { systemctl cat webdesktop.service >/dev/null 2>&1; }
printf 'System: '; . /etc/os-release; printf '%s\n' "$PRETTY_NAME"
printf 'Application: %s\n' "$WD_APP_DIR"
printf 'User: %s\n' "$WD_USER"
check 'CPU availability' nproc
check 'Memory availability' free -h
resource_check 'Filesystem free capacity' disk_capacity_status
resource_check 'Active swap capacity' swap_capacity_status
if [[ -f /etc/sysctl.d/90-webdesktop-ipv4-only.conf ]]; then
  check 'IPv4-only networking' ipv4_only_status
fi
if command -v findmnt >/dev/null; then findmnt -n -o TARGET,FSTYPE --target /; fi
if [[ -r /var/lib/webdesktop-deploy/status ]]; then
  printf '\nLast recorded installation stage (not a substitute for health checks):\n'
  # Known fields only; this file never contains credentials.
  awk -F= '$1 == "APP_DIR" || $1 == "MODULE" || $1 == "STAGE" || $1 == "EXIT_CODE" || $1 == "UPDATED_AT" {print}' /var/lib/webdesktop-deploy/status
fi
check 'Actual source commit' as_user git -C "$WD_APP_DIR" rev-parse HEAD
check 'Application source' test -f "$WD_APP_DIR/package.json"
check 'Production build' test -f "$WD_APP_DIR/.next/BUILD_ID"
check 'Next.js server build manifest' test -f "$WD_APP_DIR/.next/required-server-files.json"
check 'Installed Next.js executable' test -x "$WD_APP_DIR/node_modules/.bin/next"
check 'Committed dependency lockfile' test -f "$WD_APP_DIR/package-lock.json"
check 'noVNC assets' test -f "$WD_APP_DIR/public/novnc/core/rfb.js"
for bin in node npm Xvnc openbox dbus-run-session chromium fcitx5; do check "Executable: $bin" has_bin "$bin"; done
if command -v node >/dev/null; then node --version; fi
for package in postgresql tigervnc-standalone-server chromium fcitx5 claude-desktop; do
  dpkg-query -W -f='${Package} ${Version} ${db:Status-Status}\n' "$package" 2>/dev/null || true
done
check 'PostgreSQL service' systemctl is-active --quiet postgresql
check 'Persistent WebDesktop unit' unit_present
printf 'Unit file: '; systemctl show webdesktop -p FragmentPath --value || true
check 'WebDesktop unit directory and user' service_binding
check 'WebDesktop enabled at boot' systemctl is-enabled --quiet webdesktop
check 'WebDesktop service' systemctl is-active --quiet webdesktop
check 'Local-only TCP listener on 127.0.0.1:3000' listener_local
check 'Local HTTP endpoint' curl --noproxy '*' -fsS -o /dev/null --max-time 10 http://127.0.0.1:3000/api/health
check '.env ownership and permission 600' env_permissions
check 'Required configuration (values hidden)' as_user node "$helper" check-env "$WD_APP_DIR"
check 'Database connection (credentials hidden)' as_user node "$helper" check-db "$WD_APP_DIR"
check 'Administrator and desktop login records' as_user node "$helper" auth-state "$WD_APP_DIR"
check 'Fcitx5 profile' test -f "$WD_HOME/.config/fcitx5/profile"
check 'Fcitx5 hotkey configuration' test -f "$WD_HOME/.config/fcitx5/config"
check 'Fcitx5 pinyin configuration' test -f "$WD_HOME/.config/fcitx5/conf/pinyin.conf"
check 'Chromium Fcitx wrapper' grep -q webdesktop-fcitx-wrapper /usr/local/bin/chromium
if dpkg-query -W -f='${db:Status-Status}' claude-desktop 2>/dev/null | grep -qx installed; then
  check 'Claude executable' test -x /usr/bin/claude-desktop
  check 'Claude Fcitx wrapper' grep -q webdesktop-fcitx-wrapper /usr/local/bin/claude-desktop
else
  printf 'WARN Claude Desktop is not installed (it may have been skipped).\n'
fi
check 'Application desktop entry wrapper routing' as_user python3 "$tool_root/patches/fix-desktop.py" --check "$WD_HOME"
if [[ "$gui_smoke" == 1 ]]; then
  check 'Temporary GUI/Fcitx smoke test' as_user timeout --kill-after=5s 60s bash "$tool_root/scripts/gui-smoke.sh"
else
  printf 'GUI/Fcitx runtime and actual Chinese input: NOT TESTED (use --gui-smoke and then a manual Claude/input test).\n'
fi
printf '\nFailed checks: %s\n' "$failures"
(( failures == 0 ))
