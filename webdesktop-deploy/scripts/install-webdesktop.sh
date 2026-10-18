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
  log "Keeping existing source and branch; no git reset, checkout or pull performed."
else
  as_user mkdir -p "$(dirname -- "$WD_APP_DIR")"
  as_user git clone --branch "$WD_BRANCH" -- "$WD_REPO_URL" "$WD_APP_DIR"
fi
[[ -f "$WD_APP_DIR/package.json" && -f "$WD_APP_DIR/scripts/start.sh" ]] || die "Expected package.json and scripts/start.sh are missing."
[[ -f "$WD_APP_DIR/public/novnc/core/rfb.js" ]] || die "Bundled noVNC assets are missing."

if systemctl is-active --quiet webdesktop; then
  current="$(systemctl show webdesktop -p WorkingDirectory --value)"
  [[ "$current" == "$WD_APP_DIR" ]] || die "An existing webdesktop service belongs to another installation."
  [[ -f "$WD_APP_DIR/.next/BUILD_ID" ]] || die "An active WebDesktop has no recognizable build; refusing to modify its files."
fi

if [[ -f "$WD_APP_DIR/.next/BUILD_ID" ]]; then
  [[ "$WD_DEPLOY_MODE" != install ]] || die "A build already exists; this mode is for first installation only."
  [[ -f "$WD_APP_DIR/.env" && -d "$WD_APP_DIR/node_modules" ]] || die "Existing build is incomplete; inspect it before attempting an offline repair."
  log "Existing application preserved: no npm install, build, schema change, service stop or restart."
  log "Use check.sh to inspect its state. Application upgrades require a separate staged deployment."
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
as_user bash -c 'cd "$1"; npm ci --include=dev --no-audit --no-fund' wd "$WD_APP_DIR"
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
as_user bash -c 'cd "$1"; npm run build' wd "$WD_APP_DIR"

unit="$(mktemp)"
trap 'rm -f -- "$unit"' EXIT
python3 - "$DEPLOY_ROOT/templates/webdesktop.service" "$unit" <<'PY'
import os
import pathlib
import sys
text = pathlib.Path(sys.argv[1]).read_text()
for key in ('USER', 'GROUP', 'APP_DIR', 'HOME', 'NODE_VERSION'):
    text = text.replace('@' + key + '@', os.environ['WD_' + key])
with open(sys.argv[2], 'w', newline='\n') as handle:
    handle.write(text)
PY
backup_once /etc/systemd/system/webdesktop.service
install -m 644 "$unit" /etc/systemd/system/webdesktop.service
systemctl daemon-reload
systemctl enable --now webdesktop
healthy=0
for _ in {1..60}; do
  if curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 -o /dev/null http://127.0.0.1:3000/api/health; then
    if as_user node "$DEPLOY_ROOT/scripts/bootstrap.cjs" auth-state "$WD_APP_DIR" >/dev/null 2>&1; then
      healthy=1; break
    fi
  fi
  sleep 1
done
if [[ "$healthy" != 1 ]]; then
  systemctl --no-pager --full status webdesktop || true
  die "Local HTTP/login readiness did not pass; inspect: journalctl -u webdesktop -n 80 --no-pager"
fi
log "Local HTTP endpoint and login initialization passed."
