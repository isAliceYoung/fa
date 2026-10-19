#!/usr/bin/env bash
# Disposable booted Debian VM only; backend was built outside its 1 GiB budget.
set -Eeuo pipefail
[[ $EUID == 0 && "$(cat /proc/1/comm)" == systemd ]]
cd /root/wd-lab
apt-get update -qq
apt-get install -y --no-install-recommends postgresql polkitd iproute2 curl ca-certificates
install -m 755 /opt/webdesktop/node /usr/local/bin/node
cd /opt/webdesktop
node /root/wd-lab/webdesktop-deploy/scripts/bootstrap.cjs create-env "$PWD"
node /root/wd-lab/webdesktop-deploy/scripts/bootstrap.cjs database "$PWD"
node node_modules/drizzle-kit/bin.cjs push --force
chown -R root:wdsvc /opt/webdesktop
chmod -R o-rwx /opt/webdesktop
chmod -R g+rX /opt/webdesktop
chmod 640 /opt/webdesktop/.env
bash /root/wd-lab/webdesktop-deploy/scripts/setup-pty-layer.sh --lab
cat > /etc/systemd/system/wdlab-next.service <<'UNIT'
[Unit]
Description=Disposable lab real Next.js backend
After=postgresql.service webdesktop-guest.socket
Requires=webdesktop-guest.socket
[Service]
Type=simple
User=wdsvc
Group=wdsvc
WorkingDirectory=/opt/webdesktop
ExecStart=/usr/bin/env -i HOME=/var/lib/webdesktop USER=wdsvc LOGNAME=wdsvc PATH=/usr/local/bin:/usr/bin:/bin NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/node /opt/webdesktop/server.mjs
NoNewPrivileges=yes
CapabilityBoundingSet=
MemoryMax=384M
TasksMax=64
KillMode=control-group
TimeoutStopSec=10
Restart=no
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemd-analyze verify webdesktop-pty@1.service webdesktop-pty@2.service webdesktop-pty@1.socket webdesktop-pty@2.socket
systemd-analyze security --no-pager webdesktop-pty@1.service > /root/wd-systemd-evidence/pty-security.txt
systemctl enable --now wdlab-next.service
for attempt in {1..40}; do
  if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:3000/api/health >/dev/null; then exit 0; fi
  sleep .5
done
systemctl status --no-pager wdlab-next.service
exit 1
