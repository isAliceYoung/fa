#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
command -v node >/dev/null || die "Run install-nodejs.sh first."
command -v git >/dev/null || die "Run install-system.sh first."
git check-ref-format --branch "$WD_BRANCH" >/dev/null || die "Invalid Git branch."

if [[ -e "$WD_APP_DIR" ]]; then
  [[ -d "$WD_APP_DIR/.git" ]] || die "Existing application path is not a standalone Git checkout; refusing to overwrite."
  remote="$(as_user git -C "$WD_APP_DIR" remote get-url origin)"
  # Git may have an account name in the URL. Compare repository identity only.
  normalized="$(printf '%s' "$remote" | sed -E 's#^https://[^/@]+@github.com/#https://github.com/#; s#\.git$##')"
  [[ "$normalized" == "${WD_REPO_URL%.git}" ]] || die "Existing origin differs from the configured repository; source was not changed."
  if [[ -n "$WD_SOURCE_COMMIT" ]]; then
    current_commit="$(as_user git -C "$WD_APP_DIR" rev-parse HEAD)"
    [[ "${current_commit,,}" == "${WD_SOURCE_COMMIT,,}" ]] || die "Existing source does not match --commit; no checkout, reset or build was performed."
  fi
  log "Keeping existing source and branch; no git reset, checkout or pull performed."
else
  as_user mkdir -p "$(dirname -- "$WD_APP_DIR")"
  as_user git clone --branch "$WD_BRANCH" -- "$WD_REPO_URL" "$WD_APP_DIR"
  if [[ -n "$WD_SOURCE_COMMIT" ]]; then
    if ! as_user git -C "$WD_APP_DIR" cat-file -e "$WD_SOURCE_COMMIT^{commit}"; then
      as_user git -C "$WD_APP_DIR" fetch origin "$WD_SOURCE_COMMIT"
    fi
    as_user git -C "$WD_APP_DIR" checkout --detach "$WD_SOURCE_COMMIT"
  fi
fi
[[ -f "$WD_APP_DIR/package.json" && -f "$WD_APP_DIR/scripts/start.sh" ]] || die "Expected package.json and scripts/start.sh are missing."
[[ -f "$WD_APP_DIR/public/novnc/core/rfb.js" ]] || die "Bundled noVNC assets are missing."

service_present=0
systemctl daemon-reload || die "Could not reload systemd metadata; inspect the system service manager."
if systemctl cat webdesktop.service >/dev/null 2>&1; then
  service_present=1
  require_service_binding
fi
if systemctl is-active --quiet webdesktop; then
  [[ "$service_present" == 1 ]] || die "Active WebDesktop has no persistent service unit; refusing to modify it."
  [[ -f "$WD_APP_DIR/.next/BUILD_ID" ]] || die "An active WebDesktop has no recognizable build; refusing to modify its files."
fi

if [[ -f "$WD_APP_DIR/.next/BUILD_ID" ]]; then
  [[ -f "$WD_APP_DIR/.env" && -d "$WD_APP_DIR/node_modules" ]] || die "Existing build is incomplete; inspect it before attempting an offline repair."
  record_stage install-webdesktop validating-existing-build
  as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" check-env "$WD_APP_DIR"
  as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" check-db "$WD_APP_DIR"
  schema="$(as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" schema-state "$WD_APP_DIR")"
  [[ "$schema" == ready ]] || die "Existing build has an incomplete database schema; no schema changes were made."
  log "Existing application preserved: no npm install, build, schema change, service stop or restart."
  bash "$DEPLOY_ROOT/scripts/deploy-service.sh"
  exit 0
fi
[[ "$WD_DEPLOY_MODE" != repair ]] || die "Repair mode requires an existing application build."

[[ -f "$WD_APP_DIR/package-lock.json" ]] || die "package-lock.json is required for a new installation; generate and commit it before deploying."
as_user python3 "$DEPLOY_ROOT/patches/fix-deploy.py" "$WD_APP_DIR/src/lib/deploy.ts"
# Limit native-module compile parallelism using both CPU and RAM (1 job per 2 GiB, up to 4).
cpu_count="$(nproc)"
ram_mib="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
build_jobs=$((ram_mib / 2048))
(( build_jobs >= 1 )) || build_jobs=1
(( build_jobs <= cpu_count )) || build_jobs=cpu_count
(( build_jobs <= 4 )) || build_jobs=4
export MAKEFLAGS="-j${build_jobs}" npm_config_jobs="$build_jobs"
log "Native-module build concurrency: ${build_jobs} job(s) (${cpu_count} CPU(s), ${ram_mib} MiB RAM)."
record_stage install-webdesktop installing-dependencies
if as_user bash -c 'cd "$1"; npm ci --include=dev --no-audit --no-fund' wd "$WD_APP_DIR"; then :; else
  error_code=$?
  die "npm ci failed (exit $error_code). Check free disk/RAM, network and package.json/package-lock.json consistency; see the npm output above."
fi
as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" create-env "$WD_APP_DIR"
node "$DEPLOY_ROOT/scripts/bootstrap.cjs" database "$WD_APP_DIR"
as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" check-db "$WD_APP_DIR"
schema="$(as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" schema-state "$WD_APP_DIR")"
if [[ "$schema" == empty ]]; then
  # --force is permitted only after verifying that the database has no public tables.
  # Existing databases never receive automatic schema pushes.
  as_user bash -c 'cd "$1"; ./node_modules/.bin/drizzle-kit push --force' wd "$WD_APP_DIR"
else
  [[ "$schema" == ready ]] || die "Unexpected schema state."
  log "Keeping existing schema and data."
fi
record_stage install-webdesktop building
if as_user bash -c 'cd "$1"; npm run build' wd "$WD_APP_DIR"; then :; else
  error_code=$?
  die "Next.js build failed (exit $error_code). Check TypeScript errors above; if the process was killed, inspect kernel OOM logs and available RAM/swap."
fi
record_stage install-webdesktop built
bash "$DEPLOY_ROOT/scripts/deploy-service.sh"
