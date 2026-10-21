#!/usr/bin/env bash
# Phase 4A candidate, verified subset only. Does not replace install.sh.
set -Eeuo pipefail
exec python3 -I "$(dirname -- "${BASH_SOURCE[0]}")/scripts/isolated_deploy.py" "$@"
