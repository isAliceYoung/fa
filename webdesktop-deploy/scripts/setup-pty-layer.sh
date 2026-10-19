#!/usr/bin/env bash
# Explicit disposable Debian systemd lab only. No production installer/migration.
set -Eeuo pipefail
[[ $# == 1 && "$1" == --lab && $EUID == 0 && "$(cat /proc/1/comm)" == systemd ]] || exit 64
. /etc/os-release
[[ "$ID:$VERSION_ID" == debian:12 ]] || exit 1
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
fail() { echo "ERROR: $*" >&2; exit 1; }
[[ "$(id -u wdterm)" != "$(id -u wdguest)" && "$(id -u wdterm)" != "$(id -u wdsvc)" ]] || fail 'Distinct identities required'
for name in wdsvc wdguest wdterm; do grep -Fxq "$name" /etc/webdesktop-guest/owned-users; done
bash /usr/local/lib/webdesktop-guest/check-ssh.sh
mountpoint -q /home/wdguest/.wd-state || fail 'Protected transaction mount required'
command -v polkitd >/dev/null || [[ -x /usr/lib/polkit-1/polkitd ]] || fail 'Install polkitd in the disposable VM'
for directory in /usr/local/lib/webdesktop-pty /run/webdesktop-pty; do
  [[ ! -e "$directory" && ! -L "$directory" ]] || fail 'Existing PTY layer requires review; not overwritten'
done
install -d -m 755 /usr/local/lib/webdesktop-pty
install -d -m 750 -o root -g wdsvc /run/webdesktop-pty
install -d -m 700 -o wdsvc -g wdsvc /var/lib/webdesktop/.webdesktop-pty
for file in worker.py guard.py; do install -m 644 "$root/pty-runtime/$file" "/usr/local/lib/webdesktop-pty/$file"; done
for file in webdesktop-pty@.socket webdesktop-pty@.service webdesktop-pty-lease@.socket webdesktop-pty-lease@.service webdesktop-pty.slice; do
  [[ ! -e "/etc/systemd/system/$file" && ! -L "/etc/systemd/system/$file" ]] || fail 'Unknown unit exists'
  install -m 644 "$root/webdesktop-deploy/templates/$file" "/etc/systemd/system/$file"
done
policy=/etc/polkit-1/rules.d/00-webdesktop-pty.rules
[[ ! -e "$policy" && ! -L "$policy" ]] || fail 'Unknown policy exists'
install -m 644 "$root/webdesktop-deploy/templates/webdesktop-pty.rules" "$policy"
tmpfiles=/etc/tmpfiles.d/webdesktop-pty.conf
[[ ! -e "$tmpfiles" && ! -L "$tmpfiles" ]] || fail 'Unknown runtime policy exists'
printf 'd /run/webdesktop-pty 0750 root wdsvc -\n' > "$tmpfiles"
chmod 644 "$tmpfiles"
systemctl daemon-reload
systemctl restart polkit
systemctl enable --now webdesktop-pty@1.socket webdesktop-pty@2.socket webdesktop-pty-lease@1.socket webdesktop-pty-lease@2.socket
# Written last. Backend enables only after validating this root-owned package.
printf 'pty-v1\n' > /usr/local/lib/webdesktop-pty/ready
chmod 644 /usr/local/lib/webdesktop-pty/ready
echo 'Disposable lab PTY sockets and fixed stop-only policy prepared.'
