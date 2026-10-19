#!/usr/bin/env bash
# Distribute this file and scripts/private_repo.py together through a trusted channel.
# Phase two only: prepare/check/clone source; never invoke the legacy app installer.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "${1:-}" == --install-tools ]]; then
  (( EUID == 0 )) || { echo 'ERROR: tool installation requires root.' >&2; exit 1; }
  [[ -r /etc/os-release ]] || exit 1
  . /etc/os-release
  [[ "$ID" == debian && "$VERSION_ID" == 12 ]] || { echo 'ERROR: Debian 12 required.' >&2; exit 1; }
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=180 update
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends git openssh-client ca-certificates python3
  shift
fi
command -v python3 >/dev/null || { echo 'ERROR: python3 required; use --install-tools first.' >&2; exit 1; }
exec python3 -I "$ROOT/scripts/private_repo.py" "$@"
