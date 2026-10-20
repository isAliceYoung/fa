#!/usr/bin/env bash
# Explicitly authorized fresh test servers only. Stable install.sh is unchanged.
set -Eeuo pipefail
exec python3 -I "$(dirname -- "${BASH_SOURCE[0]}")/scripts/staging_install.py" "$@"
