#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
apt_install tigervnc-standalone-server openbox dbus-x11 x11-utils x11-xserver-utils \
  xfonts-base xterm fonts-dejavu-core xdg-utils xclip xsel xdotool python3-gi \
  gir1.2-gtk-3.0 python3-pil pcmanfm chromium chromium-l10n fonts-noto-cjk fonts-noto-color-emoji
for bin in Xvnc openbox dbus-run-session xclip xdotool chromium; do
  command -v "$bin" >/dev/null || die "GUI dependency missing: $bin"
done
log "GUI dependencies installed. WebDesktop starts per-application Xvnc sessions when needed."
