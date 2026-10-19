#!/usr/bin/env bash
# Runs INSIDE the throwaway Debian VM, never on an existing installation.
set -Eeuo pipefail
cd /root/wd-lab
[[ "$(cat /proc/1/comm)" == systemd ]]
if [[ "${1:-}" == prepare ]]; then
  bash webdesktop-deploy/scripts/setup-guest-layer.sh --lab
  systemd-tmpfiles --create /etc/tmpfiles.d/webdesktop-guest.conf
  install -d -m 750 -o root -g wdsvc /opt/webdesktop /etc/webdesktop
  printf TEST_SERVICE_SECRET > /opt/webdesktop/private-source.test
  chown root:wdsvc /opt/webdesktop/private-source.test
  chmod 640 /opt/webdesktop/private-source.test
  printf TEST_ROOT_DEPLOY_KEY > /root/deploy-key.test
  chmod 600 /root/deploy-key.test
  systemctl daemon-reload
  systemctl enable --now webdesktop-guest.socket
fi
python3 tests/linux_systemd_runtime.py "${1:-}"
