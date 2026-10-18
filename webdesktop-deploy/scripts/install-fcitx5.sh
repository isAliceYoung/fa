#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
apt_install fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 fcitx5-frontend-gtk4 \
  fcitx5-frontend-qt5 dbus-x11 fonts-noto-cjk
as_user mkdir -p "$WD_HOME/.config/fcitx5/conf"

write_user_config() {
  local target="$1"
  if [[ -e "$target" ]]; then
    log "Keeping existing input method configuration: $target"
    cat >/dev/null
  else
    as_user tee "$target" >/dev/null
  fi
}
write_user_config "$WD_HOME/.config/fcitx5/profile" <<'EOF'
[Groups/0]
Name=Default
Default Layout=us
DefaultIM=pinyin

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=pinyin
Layout=

[GroupOrder]
0=Default
EOF
write_user_config "$WD_HOME/.config/fcitx5/config" <<'EOF'
[Hotkey]
TriggerKeys=
EnumerateWithTriggerKeys=True

[Hotkey/TriggerKeys]
0=Control+space

[Behavior]
ActiveByDefault=True
ShareInputState=All
EOF
write_user_config "$WD_HOME/.config/fcitx5/conf/pinyin.conf" <<'EOF'
PageSize=7
EOF

for app in claude-desktop chromium; do
  [[ -x "/usr/bin/$app" ]] || continue
  target="/usr/local/bin/$app"
  # Preserve unrelated wrappers, and keep one backup of a previous wrapper.
  if [[ -e "$target" ]] && ! grep -q 'webdesktop-fcitx-wrapper' "$target"; then
    log "Keeping existing $target; add fcitx integration to that wrapper if needed."
    continue
  fi
  cat > "$target" <<EOF
#!/usr/bin/env bash
# webdesktop-fcitx-wrapper
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
if [[ -n "\${DISPLAY:-}" ]]; then
  fcitx5 -d >/dev/null 2>&1 || true
  sleep 0.5
fi
exec /usr/bin/$app "\$@"
EOF
  chmod 755 "$target"
done
log "Pinyin configured; Ctrl+Space switches input method inside the GUI window."
