#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
apt-get -o DPkg::Lock::Timeout=180 update
apt_install git curl ca-certificates build-essential python3 openssl xz-utils pkg-config libssl-dev util-linux

if [[ "$WD_SWAP_GB" == 2 ]] && (( $(awk '/MemTotal:/ {print $2}' /proc/meminfo) < 4194304 )); then
  if [[ -n "$(swapon --show --noheadings)" ]]; then
    log "Active swap exists; keeping it."
  elif [[ -e /swapfile ]] || grep -Eq '^[^#].*[[:space:]]swap[[:space:]]' /etc/fstab; then
    log "Existing swap file or fstab entry found; no swap changes made."
  else
    log "Creating 2 GiB swap for a machine with less than 4 GiB RAM."
    (( $(df -Pk / | awk 'NR==2 {print $4}') > 3145728 )) || die "Insufficient free space for swap; use --swap-gb 0."
    # dd creates a fully allocated file; suitable for a typical Debian ext4 VPS.
    (umask 077; dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none conv=excl)
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
  fi
fi
