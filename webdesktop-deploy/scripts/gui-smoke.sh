#!/usr/bin/env bash
# Explicit, temporary GUI check using isolated config copies and browser profile.
set -Eeuo pipefail
(( EUID != 0 )) || { echo 'Run GUI smoke test as the application user.' >&2; exit 1; }

session() {
  local work="$1" browser_pid openbox_pid fcitx_pid port state
  openbox >"$work/openbox.log" 2>&1 & openbox_pid=$!
  fcitx5 >"$work/fcitx5.log" 2>&1 & fcitx_pid=$!
  /usr/bin/chromium --no-sandbox --disable-gpu --disable-dev-shm-usage --no-first-run \
    --no-default-browser-check --user-data-dir="$work/chromium" \
    --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 \
    --app=about:blank >"$work/chromium.log" 2>&1 & browser_pid=$!
  # Capture process IDs now, while the local variables still exist.
trap "kill $browser_pid $openbox_pid $fcitx_pid 2>/dev/null || true; wait $browser_pid $openbox_pid $fcitx_pid 2>/dev/null || true" EXIT
  for _ in {1..30}; do
    if [[ -s "$work/chromium/DevToolsActivePort" ]] && kill -0 "$browser_pid" "$openbox_pid"; then
      port="$(head -n 1 "$work/chromium/DevToolsActivePort")"
      state="$(fcitx5-remote 2>/dev/null)" || state=0
      if [[ "$port" =~ ^[0-9]+$ && ( "$state" == 1 || "$state" == 2 ) ]] && \
         curl --noproxy '*' -fsS --max-time 3 -o /dev/null "http://127.0.0.1:$port/json/version"; then
        echo 'PASS: temporary Xvnc/Openbox/Chromium session and Fcitx D-Bus readiness.'
        echo 'Chinese candidate/input behavior and Claude login still require a manual GUI test.'
        return 0
      fi
    fi
    sleep 1
  done
  echo 'FAIL: temporary GUI/Fcitx smoke test did not become ready.' >&2
  for file in "$work/openbox.log" "$work/fcitx5.log" "$work/chromium.log"; do
    printf '\n%s\n' "$(basename "$file")" >&2
    tail -n 15 "$file" >&2 || true
  done
  return 1
}

if [[ "${1:-}" == --session ]]; then session "$2"; exit; fi
for bin in Xvnc openbox chromium dbus-run-session xdpyinfo fcitx5 fcitx5-remote curl; do
  command -v "$bin" >/dev/null || { echo "Missing GUI command: $bin" >&2; exit 1; }
done
work="$(mktemp -d)"
xvnc_pid=''
cleanup() {
  if [[ -n "$xvnc_pid" ]]; then kill "$xvnc_pid" 2>/dev/null || true; wait "$xvnc_pid" 2>/dev/null || true; fi
  rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export XDG_CONFIG_HOME="$work/config" XDG_STATE_HOME="$work/state" XDG_CACHE_HOME="$work/cache" XDG_DATA_HOME="$work/data" XDG_RUNTIME_DIR="$work/runtime"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
if [[ -d "$HOME/.config/fcitx5" ]]; then cp -R "$HOME/.config/fcitx5" "$XDG_CONFIG_HOME/fcitx5"; fi
display=''
for candidate in {91..199}; do
  if [[ ! -e "/tmp/.X11-unix/X$candidate" && ! -e "/tmp/.X$candidate-lock" ]] && \
     [[ -z "$(ss -H -ltn "sport = :$((5900 + candidate))")" ]]; then
    display="$candidate"; break
  fi
done
[[ -n "$display" ]] || { echo 'No free temporary X display was found.' >&2; exit 1; }
export DISPLAY=":$display"
Xvnc "$DISPLAY" -geometry 1024x768 -depth 24 -rfbport "$((5900 + display))" \
  -localhost -SecurityTypes None -nolisten tcp >"$work/xvnc.log" 2>&1 & xvnc_pid=$!
for _ in {1..20}; do
  if xdpyinfo >/dev/null 2>&1; then
    export GTK_IM_MODULE=fcitx QT_IM_MODULE=fcitx XMODIFIERS=@im=fcitx
    dbus-run-session -- bash "$0" --session "$work"
    exit
  fi
  sleep 0.5
done
tail -n 20 "$work/xvnc.log" >&2
exit 1
