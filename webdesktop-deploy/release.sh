#!/usr/bin/env bash
# Phase 4B-1 root administration of trusted release sources and immutable
# prepared releases. Does not install, switch, migrate or restart anything.
set -Eeuo pipefail
exec python3 -I "$(dirname -- "${BASH_SOURCE[0]}")/scripts/release_trust.py" "$@"
