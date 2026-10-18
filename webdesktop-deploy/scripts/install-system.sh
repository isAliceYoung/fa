#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
apt-get -o DPkg::Lock::Timeout=180 update
apt_install git curl ca-certificates build-essential python3 openssl xz-utils pkg-config libssl-dev util-linux iproute2

# Hardware preflight. nproc reflects CPUs available to this process (including affinity).
ram_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
[[ "$ram_kib" =~ ^[0-9]+$ && "$ram_kib" -gt 0 ]] || die "Could not detect RAM size."
ram_mib=$((ram_kib / 1024))
cpu_count="$(nproc)"
[[ "$cpu_count" =~ ^[0-9]+$ && "$cpu_count" -gt 0 ]] || die "Could not detect available CPUs."
log "Hardware detected: ${cpu_count} logical CPU(s), ${ram_mib} MiB RAM."
if (( ram_mib < 2048 )); then
  log "WARNING: Less than 2 GiB RAM. Swap may prevent an OOM but WebDesktop build/GUI can be very slow or fail."
fi

# --swap-gb means desired minimum TOTAL active swap, not extra swap to add.
if [[ "$WD_SWAP_GB" == auto ]]; then
  if (( ram_mib < 2048 )); then
    target_swap_gb=4
  elif (( ram_mib < 8192 )); then
    target_swap_gb=2
  else
    target_swap_gb=0
  fi
else
  target_swap_gb="$WD_SWAP_GB"
fi

# Ensure the active /swapfile will also be enabled after reboot.
ensure_swap_persistence() {
  if swapon --noheadings --output=NAME | grep -Fxq /swapfile; then
    if ! grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
      printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
      log "Active /swapfile added to /etc/fstab."
    fi
  fi
}

# Preserve active swap on all machines; create only enough additional swap if needed.
if (( target_swap_gb == 0 )); then
  log "Swap creation disabled or unnecessary; leaving any existing swap unchanged."
  exit 0
fi
active_swap_bytes="$(swapon --show --bytes --noheadings --output=SIZE | awk '{s += $1} END {printf "%.0f", s+0}')"
active_swap_mib=$((active_swap_bytes / 1048576))
required_swap_mib=$((target_swap_gb * 1024))
ensure_swap_persistence
if (( active_swap_mib >= required_swap_mib )); then
  log "Swap target met: ${active_swap_mib} MiB active (target ${required_swap_mib} MiB)."
  exit 0
fi

# Never reinitialize or overwrite a pre-existing /swapfile, even when inactive.
if [[ -e /swapfile || -L /swapfile ]] || grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
  die "Swap below target (${active_swap_mib}/${required_swap_mib} MiB), but /swapfile already exists or is in fstab. Inspect it manually; or use --swap-gb 0."
fi
needed_mib=$((required_swap_mib - active_swap_mib))
free_disk_mib="$(df -Pm / | awk 'NR==2 {print $4}')"
[[ "$free_disk_mib" =~ ^[0-9]+$ ]] || die "Could not detect free disk space."
# Keep at least 4 GiB after creating swap for dependencies and the Next.js build.
(( free_disk_mib > needed_mib + 4096 )) || die "Not enough free disk for ${needed_mib} MiB swap plus 4 GiB reserve."

log "Creating an additional ${needed_mib} MiB swapfile to reach a ${required_swap_mib} MiB target."
partial="$(mktemp /swapfile.partial.XXXXXXXX)"
trap 'rm -f -- "$partial"' EXIT
chmod 600 "$partial"
# Fully allocate the file rather than relying on sparse extents.
dd if=/dev/zero of="$partial" bs=1M count="$needed_mib" status=none
mkswap "$partial" >/dev/null
mv -- "$partial" /swapfile
if ! swapon /swapfile; then
  rm -f -- /swapfile
  die "Could not enable /swapfile (check filesystem support; no fstab entry added)."
fi
ensure_swap_persistence
trap - EXIT
log "Swap enabled; /etc/fstab persistence configured."
