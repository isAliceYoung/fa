#!/usr/bin/env bash
# Read-only effective OpenSSH policy check; run by root in the disposable lab.
set -Eeuo pipefail
(( EUID == 0 )) || { echo 'root required to validate effective sshd configuration' >&2; exit 1; }
/usr/sbin/sshd -t
for account in wdsvc wdguest wdterm; do
  for address in 127.0.0.1 198.51.100.23 2001:db8::23; do
    effective="$(/usr/sbin/sshd -T -C "user=$account,host=guest.example,addr=$address")"
    denied="$(awk '$1=="denyusers" {for(i=2;i<=NF;i++) print $i}' <<< "$effective")"
    grep -Fxq "$account" <<< "$denied" || { echo "Effective SSH denial missing for $account" >&2; exit 1; }
  done
done
echo 'Actual sshd effective policy rejects all service and execution identities.'
