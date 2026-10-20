#!/usr/bin/env bash
# Root-managed laboratory network, never called by visitor input.
set -Eeuo pipefail
[[ $EUID == 0 && $(cat /proc/1/comm) == systemd ]]
if [[ ! -d /root/wd-lab ]]; then
  . /usr/local/lib/webdesktop-guest/layer-context.sh
  staging_authorized
fi
[[ ! -e /run/netns/wd-browser ]] || { echo 'Existing browser network requires review' >&2; exit 1; }
! ip link show wdb-host >/dev/null 2>&1
! nft list table inet wd_browser >/dev/null 2>&1
nft -c -f /usr/local/lib/webdesktop-browser/network.nft
ip netns add wd-browser
ip link add wdb-host type veth peer name wdb-app
ip link set wdb-app netns wd-browser
ip addr add 10.232.0.1/30 dev wdb-host
ip link set wdb-host up
ip netns exec wd-browser ip addr add 10.232.0.2/30 dev wdb-app
ip netns exec wd-browser ip link set lo up
ip netns exec wd-browser ip link set wdb-app up
ip netns exec wd-browser ip route add default via 10.232.0.1
ip netns exec wd-browser sysctl -w net.ipv6.conf.all.disable_ipv6=1
sysctl -w net.ipv4.ip_forward=1
nft -f /usr/local/lib/webdesktop-browser/network.nft
if [[ -e /etc/netns/wd-browser ]]; then
  [[ ! -L /etc/netns/wd-browser && ! -L /etc/netns/wd-browser/resolv.conf && $(stat -c '%U:%a:%h' /etc/netns/wd-browser/resolv.conf) == root:644:1 && $(cat /etc/netns/wd-browser/resolv.conf) == 'nameserver 1.1.1.1' ]]
else
  install -d -m 755 -o root -g root /etc/netns/wd-browser
  install -m 644 /usr/local/lib/webdesktop-browser/resolv.conf /etc/netns/wd-browser/resolv.conf
fi
