#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
apt-get -o DPkg::Lock::Timeout=180 update
apt_install git curl ca-certificates build-essential python3 openssl xz-utils pkg-config libssl-dev util-linux iproute2
preflight_resources

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
  if swapon --show=NAME --noheadings | grep -Fxq /swapfile; then
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

# Read the usable capacity of all active swap areas.
active_swap_bytes="$(
  swapon --show=SIZE --bytes --noheadings |
    awk '{s += $1} END {printf "%.0f", s+0}'
)"

# Count active swap areas and detect the system page size.
active_swap_count="$(
  swapon --show=NAME --noheadings |
    awk 'NF {n++} END {print n+0}'
)"
page_size_bytes="$(getconf PAGESIZE)"

[[ "$active_swap_bytes" =~ ^[0-9]+$ ]] ||
  die "Could not detect active swap capacity."
[[ "$active_swap_count" =~ ^[0-9]+$ ]] ||
  die "Could not count active swap areas."
[[ "$page_size_bytes" =~ ^[0-9]+$ && "$page_size_bytes" -gt 0 ]] ||
  die "Could not detect memory page size."

required_swap_mib=$((target_swap_gb * 1024))
required_swap_bytes=$((required_swap_mib * 1048576))
active_swap_mib=$((active_swap_bytes / 1048576))

# Linux reserves the first page of each swap area for metadata.
# Compare nominal swap capacity rather than truncated usable MiB.
nominal_swap_bytes=$(
  (active_swap_bytes + active_swap_count * page_size_bytes)
)

ensure_swap_persistence

if (( nominal_swap_bytes >= required_swap_bytes )); then
  log "Swap target met: ${active_swap_mib} MiB usable; nominal capacity meets ${required_swap_mib} MiB target."
  exit 0
fi

# Never overwrite an existing swapfile automatically.
if [[ -e /swapfile || -L /swapfile ]] ||
   grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
  die "Swap below target, but /swapfile already exists or is in fstab. Inspect it manually; or use --swap-gb 0."
fi

# Round up the missing nominal capacity to whole MiB.
needed_mib=$(
  ( (required_swap_bytes - nominal_swap_bytes + 1048575) / 1048576 )
)

free_disk_mib="$(df -Pm / | awk 'NR==2 {print $4}')"
[[ "$free_disk_mib" =~ ^[0-9]+$ ]] || die "Could not detect free disk space."

# The 4 GiB disk reserve is advisory for low-resource test VPS instances.
# Still stop if there is not enough space to create the swapfile itself.
if (( free_disk_mib <= needed_mib )); then
  die "Insufficient disk space: ${free_disk_mib} MiB free, but ${needed_mib} MiB swap must be created."
fi

remaining_disk_mib=$((free_disk_mib - needed_mib))

if (( remaining_disk_mib < 4096 )); then
  log "WARNING: Only ${remaining_disk_mib} MiB disk space will remain after creating swap."
  log "WARNING: Less than 4 GiB will remain for dependencies and the Next.js build; installation may fail later."
fi


log "Creating an additional ${needed_mib} MiB swapfile to reach a ${required_swap_mib} MiB target."
swap_fs="$(findmnt -n -o FSTYPE --target /)"
case "$swap_fs" in
  ext2|ext3|ext4|xfs) ;;
  *) die "Automatic swap creation is not supported on filesystem '$swap_fs'. Configure swap manually or use --swap-gb 0." ;;
esac
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
