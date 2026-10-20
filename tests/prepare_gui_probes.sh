#!/usr/bin/env bash
# Feasibility experiment only. Never installed by the WebDesktop installer.
set -Eeuo pipefail
[[ $EUID == 0 && "$(cat /proc/1/comm)" == systemd && -d /root/wd-lab && -e /usr/local/lib/webdesktop-gui/ready ]]
apt-get update -qq
apt-get install -y --no-install-recommends chromium fcitx5 fcitx5-chinese-addons nftables iproute2 gnupg libnotify4 libsecret-1-0 libasound2 libpipewire-0.3-0
[[ ! -e /opt/wdgui-claude-probe && ! -e /usr/local/lib/webdesktop-gui-probe && ! -e /run/netns/wdgui-probe ]]
work="$(mktemp -d /tmp/wdgui-claude-package-XXXXXX)"
curl -fsSL --max-time 30 -o "$work/key.asc" https://downloads.claude.ai/claude-desktop/key.asc
printf 'bd70a5e4a268002704024ceba7f8446024114e94f3f0bdd11c23a9e592be81c6  %s\n' "$work/key.asc" | sha256sum -c -
install -m 644 "$work/key.asc" /usr/share/keyrings/wdgui-lab-claude.asc
printf 'deb [signed-by=/usr/share/keyrings/wdgui-lab-claude.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main\n' > /etc/apt/sources.list.d/wdgui-lab-claude.list
apt-get update -qq
cd "$work"
apt-get download claude-desktop=2.42304.2
printf '38a7d58b58a976c88ca652eaa2f7ac02c87c4a388ae30f468d72b790e6b57f90  claude-desktop_2.42304.2_amd64.deb\n' | sha256sum -c -
# Extract only: do not execute package scripts or install an updater/service.
install -d -m 755 /opt/wdgui-claude-probe /usr/local/lib/webdesktop-gui-probe
dpkg-deb -x claude-desktop_2.42304.2_amd64.deb /opt/wdgui-claude-probe
chown -R root:root /opt/wdgui-claude-probe
chmod -R go-w /opt/wdgui-claude-probe
install -m 644 /root/wd-lab/tests/gui_probe_worker.py /usr/local/lib/webdesktop-gui-probe/probe.py
python3 - <<'PY'
from pathlib import Path
root=Path('/opt/wdgui-claude-probe')
binary=root/'usr/lib/claude-desktop/claude-desktop'
assert binary.is_file() and not binary.is_symlink(),'Pinned Claude ELF not found'
with binary.open('rb') as stream:assert stream.read(4)==b'\x7fELF'
assert binary.stat().st_uid==0 and binary.stat().st_mode&0o111
Path('/usr/local/lib/webdesktop-gui-probe/claude-path').write_text(str(binary)+'\n')
Path('/root/wd-systemd-evidence/claude-package-layout.txt').write_text('binary='+str(binary)+'\n'+(root/'usr/share/applications/com.anthropic.Claude.desktop').read_text())
PY
# Root-owned network namespace, no guest CAP_NET_ADMIN or host Unix sockets.
ip netns add wdgui-probe
ip link add wdg-host type veth peer name wdg-app
ip link set wdg-app netns wdgui-probe
ip addr add 10.231.0.1/30 dev wdg-host
ip link set wdg-host up
ip netns exec wdgui-probe ip addr add 10.231.0.2/30 dev wdg-app
ip netns exec wdgui-probe ip link set lo up
ip netns exec wdgui-probe ip link set wdg-app up
ip netns exec wdgui-probe ip route add default via 10.231.0.1
ip netns exec wdgui-probe sysctl -w net.ipv6.conf.all.disable_ipv6=1
sysctl -w net.ipv4.ip_forward=1
nft -c -f /root/wd-lab/tests/gui_probe_network.nft
nft -f /root/wd-lab/tests/gui_probe_network.nft
install -d -m 755 /etc/netns/wdgui-probe
printf 'nameserver 1.1.1.1\n' > /etc/netns/wdgui-probe/resolv.conf
printf 'nameserver 1.1.1.1\n' > /usr/local/lib/webdesktop-gui-probe/resolv.conf
nft list ruleset > /root/wd-systemd-evidence/gui-probe-firewall.txt
dpkg-query -W chromium fcitx5 > /root/wd-systemd-evidence/gui-probe-versions.txt
printf 'claude-desktop extracted 2.42304.2 sha256=38a7d58b58a976c88ca652eaa2f7ac02c87c4a388ae30f468d72b790e6b57f90\n' >> /root/wd-systemd-evidence/gui-probe-versions.txt
