#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID == 0 && -d /root/wd-lab && $(cat /proc/1/comm) == systemd ]]
apt-get update -qq
apt-get install -y --no-install-recommends chromium nftables iproute2 python3-gi gir1.2-gtk-3.0 python3-pyqt5 xterm scrot
install -d -m 755 /usr/local/lib/webdesktop-test-ui
install -m 644 /root/wd-lab/tests/ime_fixture.py /usr/local/lib/webdesktop-test-ui/ime.py
install -m 644 /root/wd-lab/tests/namespace_dac_probe.py /usr/local/lib/webdesktop-test-ui/namespace-dac.py
bash /root/wd-lab/webdesktop-deploy/scripts/setup-browser-layer.sh --lab
systemd-analyze verify webdesktop-browser@1.service webdesktop-browser@1.socket webdesktop-browser-network.service webdesktop-display.service
systemd-analyze security --no-pager webdesktop-browser@1.service > /root/wd-systemd-evidence/browser-security.txt
# A laboratory aggregate, not an installer change. Keep independently trusted
# lease guards outside this workload slice so pressure cannot starve cleanup.
# Child maxima overlap; the parent enforces the actual combined RAM/Swap cap.
[[ ! -e /etc/systemd/system/webdesktop.slice ]]
cat > /etc/systemd/system/webdesktop.slice <<'UNIT'
[Unit]
Description=Disposable one-user desktop combined runtime budget
[Slice]
MemoryAccounting=yes
CPUAccounting=yes
TasksAccounting=yes
MemoryHigh=704M
MemoryMax=832M
MemorySwapMax=640M
CPUQuota=90%
TasksMax=320
UNIT
for service in wdlab-next webdesktop-guest postgresql@15-main; do
  [[ ! -e /etc/systemd/system/$service.service.d ]]
  install -d -m 755 /etc/systemd/system/$service.service.d
  printf '[Service]\nSlice=webdesktop.slice\n' > /etc/systemd/system/$service.service.d/lab-budget.conf
done
printf 'MemorySwapMax=128M\n' >> /etc/systemd/system/wdlab-next.service.d/lab-budget.conf
printf 'MemorySwapMax=32M\n' >> /etc/systemd/system/webdesktop-guest.service.d/lab-budget.conf
printf 'MemoryHigh=96M\nMemoryMax=128M\nMemorySwapMax=64M\n' >> /etc/systemd/system/postgresql@15-main.service.d/lab-budget.conf
for service in webdesktop-gui-lease@ webdesktop-pty-lease@; do
  [[ ! -e /etc/systemd/system/$service.service.d ]]
  install -d -m 755 /etc/systemd/system/$service.service.d
  printf '[Service]\nMemorySwapMax=0\n' > /etc/systemd/system/$service.service.d/lab-budget.conf
done
# Explicit test HTTPS origin; no trust in forwarded headers. Backend source/env
# remains private and the original installer is not involved.
sed -i 's,WD_PUBLIC_ORIGINS=http://127.0.0.1:3000\,http://127.0.0.1:18890 ,WD_PUBLIC_ORIGINS=http://127.0.0.1:3000\,http://127.0.0.1:18890\,https://127.0.0.1:18443 ,' /etc/systemd/system/wdlab-next.service
systemctl daemon-reload
systemctl stop wdlab-next.service webdesktop-guest.service
systemctl restart postgresql@15-main.service
systemctl restart wdlab-next.service
for attempt in {1..60}; do curl -fsS --max-time 2 http://127.0.0.1:3000/api/health >/dev/null && exit 0; sleep .5; done
exit 1
