#!/usr/bin/env bash
# Complete a first deployment, including a previous run interrupted after build.
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
[[ -f "$WD_APP_DIR/.next/BUILD_ID" && -f "$WD_APP_DIR/.env" && -d "$WD_APP_DIR/node_modules" ]] || die "Application build/configuration is incomplete; service deployment was not attempted."

if systemctl cat webdesktop.service >/dev/null 2>&1; then
  require_service_binding
else
  systemctl is-active --quiet webdesktop && die "A running service has no persistent unit; inspect it before recovery."
  log "Application is built but its service is missing; completing service configuration."
  install_service_unit || die "Could not install webdesktop.service."
  record_stage install-webdesktop service-configured
fi
systemctl enable webdesktop || die "Could not enable WebDesktop at boot."
if ! systemctl is-active --quiet webdesktop; then
  log "Matching WebDesktop service is inactive; attempting recovery without rebuilding the application."
  service_diagnostics
  systemctl reset-failed webdesktop || true
  if ! systemctl start webdesktop; then
    service_diagnostics
    die "WebDesktop service start failed; application files and credentials were preserved."
  fi
fi
wait_application_ready || die "WebDesktop failed HTTP/database/login readiness; inspect the service commands above."
record_stage install-webdesktop ready
log "WebDesktop service, /api/health and login initialization are ready."
