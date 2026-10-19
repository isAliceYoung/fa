#!/usr/bin/env bash
# Explicit opt-in for a disposable 3A lab. No application or existing-user migration.
set -Eeuo pipefail
[[ "${1:-}" == --lab && $# == 1 ]] || { echo 'Usage: sudo bash setup-guest-layer.sh --lab (disposable Debian 12 only)' >&2; exit 64; }
(( EUID == 0 )) || { echo 'root required' >&2; exit 1; }
. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 12 ]] || { echo 'Debian 12 required' >&2; exit 1; }
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
fail() { echo "ERROR: $*" >&2; exit 1; }
for directory in /etc/webdesktop-guest /usr/local/lib/webdesktop-guest /run/webdesktop-guest; do
  [[ ! -L "$directory" ]] || fail "Refusing symlink: $directory"
  if [[ -e "$directory" ]]; then
    [[ -d "$directory" && "$(stat -c %U "$directory")" == root ]] || fail "Unexpected directory owner/type: $directory"
    permissions="$(stat -c %a "$directory")"
    (( (8#$permissions & 022) == 0 )) || fail 'Existing setup directory is writable by another identity'
  fi
done
install -d -m 700 /etc/webdesktop-guest
record=/etc/webdesktop-guest/owned-users
if [[ -e "$record" || -L "$record" ]]; then
  [[ ! -L "$record" && -f "$record" && "$(stat -c '%U:%a' "$record")" == root:600 ]] || fail 'Unsafe identity record'
else
  install -m 600 /dev/null "$record"
fi
for name in wdsvc wdguest; do
  if getent passwd "$name" >/dev/null; then
    grep -Fxq "$name" "$record" || fail "Existing $name is not owned by this lab setup; no adoption attempted"
  fi
done
for name in wdsvc wdguest; do
  if [[ "$name" == wdsvc ]]; then user_home=/var/lib/webdesktop; else user_home=/home/wdguest; fi
  if ! getent passwd "$name" >/dev/null; then
    [[ ! -e "$user_home" && ! -L "$user_home" ]] || fail "Existing home requires manual review: $user_home"
    useradd --system --user-group --no-create-home --home-dir "$user_home" --shell /usr/sbin/nologin --password '!' "$name"
    printf '%s\n' "$name" >> "$record"
    install -d -m 700 -o "$name" -g "$name" "$user_home"
  fi
  uid="$(id -u "$name")"; gid="$(id -g "$name")"
  [[ "$uid" -gt 0 && "$(id -G "$name")" == "$gid" ]] || fail 'Unexpected UID or supplementary groups'
  [[ "$(getent passwd "$name" | cut -d: -f6)" == "$user_home" && "$(getent passwd "$name" | cut -d: -f7)" == /usr/sbin/nologin ]] || fail 'Unexpected account settings'
  [[ ! -L "$user_home" && "$(stat -c '%U:%a' "$user_home")" == "$name:700" ]] || fail 'Unsafe account home'
done
[[ "$(id -u wdsvc)" != "$(id -u wdguest)" && "$(id -g wdsvc)" != "$(id -g wdguest)" ]] || fail 'Distinct identities required'
install -d -m 755 /usr/local/lib/webdesktop-guest
for file in agent.py secure_fs.py; do
  target="/usr/local/lib/webdesktop-guest/$file"
  [[ ! -L "$target" ]] || fail 'Guest runtime must not be a symlink'
  [[ ! -e "$target" || "$(stat -c '%U:%a:%h' "$target")" == root:644:1 ]] || fail 'Unexpected runtime ownership/permissions/links'
  if [[ -e "$target" ]]; then cmp -s "$REPO_ROOT/guest-runtime/$file" "$target" || fail 'Existing guest runtime differs; use a reviewed update'; else install -m 644 "$REPO_ROOT/guest-runtime/$file" "$target"; fi
done
for file in webdesktop-guest.socket webdesktop-guest.service; do
  target="/etc/systemd/system/$file"
  [[ ! -L "$target" ]] || fail 'Unit must not be a symlink'
  if [[ -e "$target" ]]; then cmp -s "$REPO_ROOT/webdesktop-deploy/templates/$file" "$target" || fail 'Existing guest unit differs'; else install -m 644 "$REPO_ROOT/webdesktop-deploy/templates/$file" "$target"; fi
done
install -d -m 750 -o root -g wdsvc /run/webdesktop-guest
tmpfiles=/etc/tmpfiles.d/webdesktop-guest.conf
expected='d /run/webdesktop-guest 0750 root wdsvc -'
[[ ! -L "$tmpfiles" ]] || fail 'Unsafe tmpfiles configuration'
if [[ -e "$tmpfiles" ]]; then [[ "$(cat "$tmpfiles")" == "$expected" ]] || fail 'Existing tmpfiles config differs'; else printf '%s\n' "$expected" > "$tmpfiles"; chmod 644 "$tmpfiles"; fi
printf '3A lab identities and guest runtime prepared; no existing app migrated, no sshd changed, no services started.\n'
