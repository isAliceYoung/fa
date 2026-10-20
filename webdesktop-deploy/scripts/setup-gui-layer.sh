#!/usr/bin/env bash
# Explicitly disposable Debian 12 lab. Never adopt an existing identity/runtime.
set -Eeuo pipefail
[[ $# == 1 && "$1" == --lab && $EUID == 0 && "$(cat /proc/1/comm)" == systemd ]] || exit 64
. /etc/os-release
[[ "$ID:$VERSION_ID" == debian:12 ]] || exit 1
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
fail() { echo "ERROR: $*" >&2; exit 1; }
for account in wdsvc wdguest wdterm; do grep -Fxq "$account" /etc/webdesktop-guest/owned-users; done
for bin in Xtigervnc openbox pcmanfm xauth dbus-daemon fcitx5; do command -v "$bin" >/dev/null || fail "Missing $bin"; done
for path in /usr/local/lib/webdesktop-gui /run/webdesktop-gui /run/webdesktop-display /var/lib/webdesktop-gui /home/wdguest/.gui; do
  [[ ! -e "$path" && ! -L "$path" ]] || fail 'Existing GUI path requires review'
done
! getent passwd wdgui >/dev/null || fail 'Existing GUI identity requires review'
for file in webdesktop-gui@.service webdesktop-gui@.socket webdesktop-gui-lease@.service webdesktop-gui-lease@.socket webdesktop-gui-state.service webdesktop-gui.slice webdesktop-display.service; do
  [[ ! -e "/etc/systemd/system/$file" && ! -L "/etc/systemd/system/$file" ]] || fail 'Existing unit requires review'
done
[[ ! -e /etc/polkit-1/rules.d/00-webdesktop-gui.rules && ! -e /etc/tmpfiles.d/webdesktop-gui.conf ]] || fail 'Existing GUI policy requires review'
ssh_file=/etc/ssh/sshd_config.d/00-webdesktop-identities.conf
[[ ! -L "$ssh_file" && "$(stat -c '%U:%a:%h' "$ssh_file")" == root:644:1 && "$(cat "$ssh_file")" == 'DenyUsers wdsvc wdguest wdterm' ]] || fail 'Unknown SSH policy'
mountpoint -q /home/wdguest/.wd-state || fail 'Guest transaction mount required'
cmp -s "$root/webdesktop-deploy/templates/webdesktop-pty.rules" /etc/polkit-1/rules.d/00-webdesktop-pty.rules || fail 'Reviewed deny-by-default service policy required'
useradd --system --user-group --no-create-home --home-dir /var/lib/webdesktop-gui --shell /usr/sbin/nologin --password '!' wdgui
printf 'wdgui\n' >> /etc/webdesktop-guest/owned-users
install -d -m 700 -o wdgui -g wdgui /var/lib/webdesktop-gui
install -d -m 700 -o wdgui -g wdguest /home/wdguest/.gui
chmod 00700 /home/wdguest/.gui
printf 'DenyUsers wdsvc wdguest wdterm wdgui\n' > "$ssh_file"
/usr/sbin/sshd -t
bash /usr/local/lib/webdesktop-guest/check-ssh.sh
systemctl reload ssh
install -d -m 755 /usr/local/lib/webdesktop-gui
install -d -m 750 -o root -g wdsvc /run/webdesktop-gui
install -d -m 755 -o root -g root /run/webdesktop-display
install -d -m 700 -o wdsvc -g wdsvc /var/lib/webdesktop/.webdesktop-gui
for file in worker.py guard.py openbox.xml dbus.conf; do install -m 644 "$root/gui-runtime/$file" "/usr/local/lib/webdesktop-gui/$file"; done
for file in webdesktop-gui@.service webdesktop-gui@.socket webdesktop-gui-lease@.service webdesktop-gui-lease@.socket webdesktop-gui-state.service webdesktop-gui.slice webdesktop-display.service; do install -m 644 "$root/webdesktop-deploy/templates/$file" "/etc/systemd/system/$file"; done
install -m 644 "$root/webdesktop-deploy/templates/webdesktop-gui.rules" /etc/polkit-1/rules.d/00-webdesktop-gui.rules
printf 'd /run/webdesktop-gui 0750 root wdsvc -\n' > /etc/tmpfiles.d/webdesktop-gui.conf
printf 'd /run/webdesktop-display 0755 root root -\n' >> /etc/tmpfiles.d/webdesktop-gui.conf
systemctl daemon-reload
systemctl restart polkit
systemctl enable --now webdesktop-display.service webdesktop-gui-state.service webdesktop-gui@1.socket webdesktop-gui-lease@1.socket
systemctl restart webdesktop-guest.service
printf 'gui-basic-v1\n' > /usr/local/lib/webdesktop-gui/ready
chmod 644 /usr/local/lib/webdesktop-gui/ready
echo 'Disposable basic GUI prepared; no browser, clipboard or production migration enabled.'
