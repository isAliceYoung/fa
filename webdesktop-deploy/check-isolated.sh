#!/usr/bin/env bash
set -Eeuo pipefail
exec python3 -I "$(dirname -- "${BASH_SOURCE[0]}")/scripts/staging_check.py" --isolated "$@"
