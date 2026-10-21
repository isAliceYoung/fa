#!/usr/bin/env bash
# New disposable labs only. Never formats or mounts over existing guest data.
set -Eeuo pipefail
. "$(dirname -- "${BASH_SOURCE[0]}")/layer-context.sh"
authorize_layer "$@" || { echo "root + explicit --lab or authorized --staging required" >&2; exit 64; }
home=/home/wdguest
volume=/var/lib/webdesktop-guest-volume
image="$volume/workspace.ext4"
unit=/etc/systemd/system/home-wdguest.mount
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
fail() { echo "ERROR: $*" >&2; exit 1; }
[[ ! -L "$home" && -d "$home" && ( "$(stat -c '%U:%a' "$home")" == wdguest:700 || "$(stat -c '%U:%a' "$home")" == wdguest:2770 ) ]] || fail 'Unsafe guest home'
if mountpoint -q "$home"; then
  [[ ! -L "$image" && -f "$image" && "$(stat -c '%U:%a:%h' "$image")" == root:600:1 ]] || fail 'Unknown backing image'
  device="$(findmnt -n -o SOURCE --target "$home")"
  [[ "$(losetup -n -O BACK-FILE "$device")" == "$image" ]] || fail 'Unknown existing mount'
  mountpoint -q "$home/.wd-state" || fail 'Private state mount missing; manual recovery required'
  bash "$script_dir/setup-guest-ssh-deny.sh" "$layer_mode"
  echo 'Existing lab guest volume preserved.'
  exit 0
fi
[[ ! -e "$volume" && ! -L "$volume" && ! -e "$unit" && ! -L "$unit" ]] || fail 'Existing unmounted state requires manual review'
[[ -z "$(find "$home" -mindepth 1 -print -quit)" ]] || fail 'Guest home is not empty; no migration attempted'
available="$(df -PB1 /var/lib | awk 'NR==2 {print $4}')"
workspace_mib=512
if [[ "$layer_mode" == --staging ]]; then
  workspace_mib="$(python3 -I -c 'import json,os,stat; p="/etc/webdesktop-resources/policy.json"; s=os.lstat(p); assert s.st_uid==0 and stat.S_ISREG(s.st_mode) and s.st_nlink==1 and not s.st_mode&0o022; n=json.load(open(p))["workspace_mib"]; assert type(n)==int and 512<=n<=16384; print(n)')"
fi
(( available >= workspace_mib*1048576+1073741824 )) || fail 'Workspace would consume host reserve'
install -d -m 700 "$volume"
umask 077
(set -o noclobber; : > "$image")
fallocate -l "${workspace_mib}M" "$image"
mkfs.ext4 -q -F -m 0 -N "$((workspace_mib*32))" "$image"
cat > "$unit" <<'UNIT'
[Unit]
Description=Bounded guest workspace (root-managed capacity)
Before=webdesktop-guest.service
[Mount]
What=/var/lib/webdesktop-guest-volume/workspace.ext4
Where=/home/wdguest
Type=ext4
Options=loop,rw,nosuid,nodev,noexec
[Install]
WantedBy=local-fs.target
UNIT
chmod 644 "$unit"
if [[ "$(cat /proc/1/comm)" == systemd ]]; then
  systemctl daemon-reload
  systemctl enable --now home-wdguest.mount
else
  mount -o loop,rw,nosuid,nodev,noexec "$image" "$home"
fi
chown wdguest:wdguest "$home"
chmod 2770 "$home"
install -d -m 700 -o wdguest -g wdguest "$home/.wd-state"
# GNU install/chmod preserve directory setgid unless explicitly cleared.
chmod 00700 "$home/.wd-state"
if [[ "$(cat /proc/1/comm)" == systemd ]]; then
  systemctl enable --now webdesktop-guest-state.service
else
  mount --bind "$home/.wd-state" "$home/.wd-state"
fi
bash "$script_dir/setup-guest-ssh-deny.sh" "$layer_mode"
printf 'Fresh lab guest workspace mounted with kernel filesystem capacity/inode limits.\n'
