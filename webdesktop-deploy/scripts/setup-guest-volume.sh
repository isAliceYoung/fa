#!/usr/bin/env bash
# New disposable labs only. Never formats or mounts over existing guest data.
set -Eeuo pipefail
[[ $# == 1 && "$1" == --lab && $EUID == 0 ]] || { echo 'root + --lab required' >&2; exit 64; }
. /etc/os-release
[[ "$ID:$VERSION_ID" == debian:12 ]] || exit 1
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
  bash "$script_dir/setup-guest-ssh-deny.sh" --lab
  echo 'Existing lab guest volume preserved.'
  exit 0
fi
[[ ! -e "$volume" && ! -L "$volume" && ! -e "$unit" && ! -L "$unit" ]] || fail 'Existing unmounted state requires manual review'
[[ -z "$(find "$home" -mindepth 1 -print -quit)" ]] || fail 'Guest home is not empty; no migration attempted'
available="$(df -PB1 /var/lib | awk 'NR==2 {print $4}')"
(( available >= 1073741824 )) || fail 'At least 1 GiB host space required'
install -d -m 700 "$volume"
umask 077
(set -o noclobber; : > "$image")
fallocate -l 512M "$image"
mkfs.ext4 -q -F -m 0 -N 16384 "$image"
cat > "$unit" <<'UNIT'
[Unit]
Description=Bounded new-lab guest workspace (512 MiB)
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
bash "$script_dir/setup-guest-ssh-deny.sh" --lab
printf 'Fresh lab guest workspace mounted with kernel filesystem capacity/inode limits.\n'
