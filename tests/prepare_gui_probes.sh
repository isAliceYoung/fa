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
import shlex
root=Path('/opt/wdgui-claude-probe')
choices=[]
for p in root.rglob('*.desktop'):
 for line in p.read_text(errors='replace').splitlines():
  if line.startswith('Exec='):
   target=shlex.split(line[5:])[0]
   candidate=root/target.lstrip('/')
   if candidate.is_file() and not candidate.is_symlink():choices.append(candidate)
if not choices:
 choices=[p for p in (root/'opt').rglob('*') if p.is_file() and p.name.lower() in ('claude','claude-desktop')]
assert choices,'Claude executable not found'
Path('/usr/local/lib/webdesktop-gui-probe/claude-path').write_text(str(choices[0])+'\n')
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
nft -f - <<'NFT'
table inet wdgui_probe {
 chain input { type filter hook input priority -50; policy accept; iifname "wdg-host" reject; }
 chain forward { type filter hook forward priority -50; policy accept;
  iifname "wdg-host" meta nfproto ipv6 drop
  iifname "wdg-host" ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.168.0.0/16, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4 } reject
  iifname "wdg-host" tcp dport { 80, 443 } accept
  iifname "wdg-host" udp dport 53 accept
  iifname "wdg-host" drop
  oifname "wdg-host" ct state established,related accept
  oifname "wdg-host" drop
 }
}
table ip wdgui_probe_nat { chain postrouting { type nat hook postrouting priority srcnat; ip saddr 10.231.0.2 masquerade; } }
NFT
install -d -m 755 /etc/netns/wdgui-probe
printf 'nameserver 1.1.1.1\n' > /etc/netns/wdgui-probe/resolv.conf
printf 'nameserver 1.1.1.1\n' > /usr/local/lib/webdesktop-gui-probe/resolv.conf
nft list ruleset > /root/wd-systemd-evidence/gui-probe-firewall.txt
dpkg-query -W chromium fcitx5 > /root/wd-systemd-evidence/gui-probe-versions.txt
printf 'claude-desktop extracted 2.42304.2 sha256=38a7d58b58a976c88ca652eaa2f7ac02c87c4a388ae30f468d72b790e6b57f90\n' >> /root/wd-systemd-evidence/gui-probe-versions.txt
