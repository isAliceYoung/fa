#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
[[ -n "${LOG_FILE:-}" ]] || die "LOG_FILE is required; run install.sh."
modules=(install-system install-nodejs install-postgres install-gui install-claude install-fcitx5 install-webdesktop)
if [[ "$WD_DEPLOY_MODE" == service ]]; then
  # Keep the existing-build checks before deploy-service.sh; do not install packages.
  modules=(install-webdesktop)
fi
for module in "${modules[@]}"; do
  log "========== START: $module =========="
  record_stage "$module" started || true
  if bash "$DEPLOY_ROOT/scripts/$module.sh"; then
    log "========== SUCCESS: $module =========="
    record_stage "$module" complete || true
  else
    error_code=$?
    record_stage "$module" failed "$error_code" || true
    log "========== FAILED: $module =========="
    log "Exit code: $error_code"
    log "Installation log: $LOG_FILE"
    printf '\nERROR: Installation stopped at module [%s].\n' "$module" >&2
    printf 'Inspect the module output above for the underlying error.\n' >&2
    exit "$error_code"
  fi
done
