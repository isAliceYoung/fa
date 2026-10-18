#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
apt_install postgresql postgresql-contrib
systemctl enable --now postgresql
pg_isready -h 127.0.0.1 -p 5432 >/dev/null || die "PostgreSQL is not accepting local connections on port 5432."
log "PostgreSQL ready; application role and database are provisioned after .env creation."
