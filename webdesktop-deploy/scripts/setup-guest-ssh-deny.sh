#!/usr/bin/env bash
# Only the disposable lab. A global DenyUsers directive contains no Match block.
set -Eeuo pipefail
[[ $# == 1 && "$1" == --lab && $EUID == 0 ]] || exit 64
. /etc/os-release
[[ "$ID:$VERSION_ID" == debian:12 ]] || exit 1
grep -Fxq wdsvc /etc/webdesktop-guest/owned-users
grep -Fxq wdguest /etc/webdesktop-guest/owned-users
file=/etc/ssh/sshd_config.d/00-webdesktop-identities.conf
marker=/usr/local/lib/webdesktop-guest/ssh-denied
expected='DenyUsers wdsvc wdguest wdterm'
install -d -m 755 /run/sshd
before="$(/usr/sbin/sshd -T -C user=root,host=localhost,addr=127.0.0.1)"
created=0
if [[ -e "$file" || -L "$file" ]]; then
  [[ ! -L "$file" && "$(stat -c '%U:%a:%h' "$file")" == root:644:1 && "$(cat "$file")" == "$expected" ]] || exit 1
else
  # Do not replace or weaken an unknown pre-existing DenyUsers policy.
  ! grep -q '^denyusers ' <<< "$before" || { echo 'Existing SSH deny policy needs review' >&2; exit 1; }
  (umask 022; set -o noclobber; printf '%s\n' "$expected" > "$file")
  created=1
fi
rollback() {
  if (( created )) && [[ ! -L "$file" && "$(cat "$file")" == "$expected" ]]; then rm -- "$file"; fi
}
trap rollback ERR
/usr/sbin/sshd -t
after="$(/usr/sbin/sshd -T -C user=root,host=localhost,addr=127.0.0.1)"
[[ "$(grep -v '^denyusers ' <<< "$before")" == "$(grep -v '^denyusers ' <<< "$after")" ]]
for name in wdsvc wdguest wdterm; do
  effective="$(/usr/sbin/sshd -T -C "user=$name,host=localhost,addr=127.0.0.1")"
  denied="$(awk '$1=="denyusers" {for(i=2;i<=NF;i++) print $i}' <<< "$effective" | sort)"
  [[ "$denied" == $'wdguest\nwdsvc\nwdterm' ]]
done
if [[ "$(cat /proc/1/comm)" == systemd ]]; then systemctl reload ssh; fi
bash /usr/local/lib/webdesktop-guest/check-ssh.sh
if [[ -e "$marker" || -L "$marker" ]]; then
  [[ ! -L "$marker" && "$(stat -c '%U:%a:%h' "$marker")" == root:644:1 && "$(cat "$marker")" == 'wdsvc wdguest wdterm' ]]
else
  (umask 022; set -o noclobber; printf 'wdsvc wdguest wdterm\n' > "$marker")
fi
trap - ERR
printf 'Lab SSH rejects service/guest identities; administrator settings preserved.\n'
