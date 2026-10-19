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


# Ensure an active /swapfile remains enabled after reboot.
ensure_swap_persistence() {
  if swapon --show=NAME --noheadings | grep -Fxq /swapfile; then
    if ! grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
      printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
      log "Active /swapfile added to /etc/fstab."
    fi
  fi
}

# Read existing active Swap before choosing the target.
active_swap_bytes="$(
  swapon --show=SIZE --bytes --noheadings |
    awk '{s += $1} END {printf "%.0f", s+0}'
)"
active_swap_count="$(
  swapon --show=NAME --noheadings |
    awk 'NF {n++} END {print n+0}'
)"
page_size_bytes="$(getconf PAGESIZE)"

[[ "$active_swap_bytes" =~ ^[0-9]+$ ]] ||
  die "Cannot determine active Swap bytes."
[[ "$active_swap_count" =~ ^[0-9]+$ ]] ||
  die "Cannot count active Swap areas."
[[ "$page_size_bytes" =~ ^[0-9]+$ && "$page_size_bytes" -gt 0 ]] ||
  die "Cannot detect memory page size."

active_swap_mib=$((active_swap_bytes / 1048576))

# Account for Swap header pages when comparing nominal capacities.
nominal_swap_bytes=$((active_swap_bytes + active_swap_count * page_size_bytes))

ensure_swap_persistence

# Reserve 6 GiB as a planning budget for the remaining installation.
# This is not a hard minimum required to run WebDesktop.
disk_budget_mib=6144
free_disk_mib="$(df -Pm -- / | awk 'NR==2 {print $4}')"

[[ "$free_disk_mib" =~ ^[0-9]+$ ]] ||
  die "Cannot detect free disk space."

app_disk_path="$(application_disk_path)"
app_free_disk_mib="$(df -Pm -- "$app_disk_path" | awk 'NR==2 {print $4}')"
[[ "$app_free_disk_mib" =~ ^[0-9]+$ ]] || die "Cannot detect free disk space for the application."
root_device="$(stat -L -c '%d' -- /)"
app_device="$(stat -L -c '%d' -- "$app_disk_path")"
[[ "$root_device" =~ ^[0-9]+$ && "$app_device" =~ ^[0-9]+$ ]] || die "Cannot identify the root/application filesystems."
log "Swap planning: root ${free_disk_mib} MiB free; application filesystem ${app_free_disk_mib} MiB free (checked at $app_disk_path)."

# In auto mode an existing managed path fixes the preservation policy before
# choosing capacity candidates. Never advertise a larger, unachievable target.
if [[ "$WD_SWAP_GB" == auto ]] && { [[ -e /swapfile || -L /swapfile ]] ||
     grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; }; then
  if swapon --show=NAME --noheadings | grep -Fxq /swapfile; then
    log "Auto Swap: preserving existing /swapfile; active usable Swap: ${active_swap_mib} MiB. No target reselection or resizing."
  else
    log "WARNING: Existing /swapfile is not active; preserving it without formatting or activation. Inspect it manually."
  fi
  if (( free_disk_mib < disk_budget_mib || app_free_disk_mib < disk_budget_mib )); then
    log "WARNING: The ${disk_budget_mib} MiB installation budget is not met on the root/application filesystems; existing Swap is preserved."
  fi
  if (( ram_mib < 2048 && nominal_swap_bytes < 1073741824 )); then
    log "WARNING: Low memory with less than 1 GiB active Swap; the Next.js build may run out of memory (OOM). Inspect Swap manually."
  fi
  exit 0
fi

if [[ "$WD_SWAP_GB" == auto ]]; then
  if (( ram_mib < 2048 )); then
    candidates=(4 2 1 0)
  elif (( ram_mib < 8192 )); then
    candidates=(2 1 0)
  else
    candidates=(0)
  fi

  target_swap_gb=0
  budget_satisfied=0

  for candidate in "${candidates[@]}"; do
    candidate_bytes=$((candidate * 1073741824))
    missing_bytes=$((candidate_bytes - nominal_swap_bytes))

    if (( missing_bytes < 0 )); then
      missing_bytes=0
    fi

    candidate_needed_mib=$(((missing_bytes + 1048575) / 1048576))

    app_remaining_mib="$app_free_disk_mib"
    if [[ "$root_device" == "$app_device" ]]; then
      app_remaining_mib=$((app_remaining_mib - candidate_needed_mib))
    fi

    if (( free_disk_mib - candidate_needed_mib >= disk_budget_mib && app_remaining_mib >= disk_budget_mib )); then
      target_swap_gb="$candidate"
      budget_satisfied=1
      break
    fi
  done

  if (( budget_satisfied == 0 )); then
    log "WARNING: Cannot preserve the ${disk_budget_mib} MiB disk budget on the root/application filesystems."
    log "WARNING: Skipping new Swap allocation; existing Swap is preserved."
  fi

  if (( ram_mib < 2048 && target_swap_gb == 0 && nominal_swap_bytes < 1073741824 )); then
    log "WARNING: Low memory with less than 1 GiB active Swap: disk budget prevents new Swap."
    log "WARNING: The Next.js build may run out of memory (OOM); free disk space or use a larger VPS before building."
  fi

  log "Auto Swap target: ${target_swap_gb} GiB; active usable Swap: ${active_swap_mib} MiB."
else
  target_swap_gb="$WD_SWAP_GB"
  log "Explicit Swap target: ${target_swap_gb} GiB."
fi

required_swap_mib=$((target_swap_gb * 1024))
required_swap_bytes=$((required_swap_mib * 1048576))

# Zero target means no additional Swap, not disabling existing Swap.
if (( target_swap_gb == 0 )); then
  log "No additional Swap requested; existing Swap is unchanged."
  exit 0
fi

if (( nominal_swap_bytes >= required_swap_bytes )); then
  log "Swap target met: ${active_swap_mib} MiB usable; target ${required_swap_mib} MiB nominal."
  exit 0
fi

# Never overwrite an existing /swapfile automatically.
if [[ -e /swapfile || -L /swapfile ]] ||
   grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then

  if [[ "$WD_SWAP_GB" == auto ]]; then
    log "WARNING: Existing /swapfile is below the automatic target."
    log "WARNING: Preserving existing Swap without resizing."
    exit 0
  fi

  die "Swap is below the explicit target, but /swapfile exists or is in fstab. Inspect it manually."
fi

# Calculate the additional allocation, rounded up to whole MiB.
needed_mib=$(((required_swap_bytes - nominal_swap_bytes + 1048575) / 1048576))

# Only insufficient space for the Swap file itself is a hard failure.
if (( free_disk_mib <= needed_mib )); then
  die "Insufficient disk space to allocate ${needed_mib} MiB of additional Swap."
fi

remaining_disk_mib=$((free_disk_mib - needed_mib))

if (( remaining_disk_mib < disk_budget_mib )); then
  log "WARNING: Approximately ${remaining_disk_mib} MiB will remain after Swap allocation."
  log "WARNING: The ${disk_budget_mib} MiB installation budget is not met; npm/build may fail."
fi

app_remaining_mib="$app_free_disk_mib"
if [[ "$root_device" == "$app_device" ]]; then
  app_remaining_mib=$((app_remaining_mib - needed_mib))
fi
if (( app_remaining_mib < disk_budget_mib )); then
  log "WARNING: Application filesystem will have approximately ${app_remaining_mib} MiB free; the ${disk_budget_mib} MiB installation budget is not met."
fi

log "Creating an additional ${needed_mib} MiB swapfile (target: ${required_swap_mib} MiB nominal)."

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
