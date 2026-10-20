#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID == 0 && "$(cat /proc/1/comm)" == systemd ]]
cd /root/wd-lab
apt-get update -qq
apt-get install -y --no-install-recommends tigervnc-standalone-server openbox pcmanfm dbus-x11 xauth x11-utils xdotool xclip xfonts-base fonts-dejavu-core
install -d -m 750 -o root -g wdsvc /opt/webdesktop/.git
printf 'ref: refs/heads/lab-test-only\n' > /opt/webdesktop/.git/HEAD
chown root:wdsvc /opt/webdesktop/.git/HEAD
chmod 640 /opt/webdesktop/.git/HEAD
bash webdesktop-deploy/scripts/setup-gui-layer.sh --lab
systemd-analyze verify webdesktop-gui@1.service webdesktop-gui@1.socket webdesktop-gui-lease@1.service webdesktop-gui-lease@1.socket webdesktop-gui-state.service webdesktop-gui.slice
systemd-analyze security --no-pager webdesktop-gui@1.service > /root/wd-systemd-evidence/gui-security.txt
systemctl restart wdlab-next.service
for attempt in {1..40}; do
  if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:3000/api/health >/dev/null; then exit 0; fi
  sleep .5
done
exit 1
