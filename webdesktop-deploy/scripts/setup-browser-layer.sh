#!/usr/bin/env bash
# Fresh disposable Debian lab only; no production installer integration.
set -Eeuo pipefail
. "$(dirname -- "${BASH_SOURCE[0]}")/layer-context.sh"
authorize_layer "$@" || { echo "root + explicit --lab or authorized --staging required" >&2; exit 64; }
[[ $(cat /proc/1/comm) == systemd ]]
[[ "$layer_mode" != --lab || -d /root/wd-lab ]]
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ -e /usr/local/lib/webdesktop-gui/ready && ! -e /usr/local/lib/webdesktop-browser && ! -e /run/webdesktop-browser ]]
for file in webdesktop-browser@.service webdesktop-browser@.socket webdesktop-browser-network.service; do [[ ! -e /etc/systemd/system/$file ]]; done
for bin in chromium nft ip xdotool; do command -v "$bin" >/dev/null; done
install -d -m 755 -o root -g root /usr/local/lib/webdesktop-browser
install -d -m 750 -o root -g wdsvc /run/webdesktop-browser
install -m 644 "$root/gui-runtime/browser-worker.py" /usr/local/lib/webdesktop-browser/worker.py
recommended=/etc/chromium/policies/recommended/webdesktop.json
[[ ! -e "$recommended" && ! -L "$recommended" ]]
install -d -m 755 /etc/chromium/policies/recommended
printf '%s\n' '{"DefaultSearchProviderEnabled":true,"DefaultSearchProviderName":"Google","DefaultSearchProviderKeyword":"google.com","DefaultSearchProviderSearchURL":"https://www.google.com/search?q={searchTerms}"}' > "$recommended"
chmod 644 "$recommended"
install -m 755 "$root/gui-runtime/browser-network.sh" /usr/local/lib/webdesktop-browser/network.sh
install -m 644 "$root/webdesktop-deploy/templates/webdesktop-browser.nft" /usr/local/lib/webdesktop-browser/network.nft
printf 'nameserver 1.1.1.1\n' > /usr/local/lib/webdesktop-browser/resolv.conf
chmod 644 /usr/local/lib/webdesktop-browser/resolv.conf
for file in webdesktop-browser@.service webdesktop-browser@.socket webdesktop-browser-network.service; do install -m 644 "$root/webdesktop-deploy/templates/$file" /etc/systemd/system/; done
printf 'd /run/webdesktop-browser 0750 root wdsvc -\n' > /etc/tmpfiles.d/webdesktop-browser.conf
systemctl daemon-reload
systemctl enable --now webdesktop-browser-network.service webdesktop-browser@1.socket
printf 'chromium-visible-v1\n' > /usr/local/lib/webdesktop-browser/ready
chmod 644 /usr/local/lib/webdesktop-browser/ready
