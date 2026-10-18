#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
if [[ "$WD_SKIP_CLAUDE" == 1 ]]; then log "Claude Desktop skipped by option."; exit 0; fi
apt_install curl ca-certificates
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
# Same APT source and commands as the user's successful google_cloud.txt record.
# Fetch to a temporary file first so a network error cannot truncate an existing key.
curl -fsSL --retry 3 -o "$work/key.asc" https://downloads.claude.ai/claude-desktop/key.asc || \
  die "Claude APT key download failed at downloads.claude.ai. Check network/TLS, or retry with --skip-claude."
grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$work/key.asc" || die "Claude APT key is not an ASCII-armored public key."
install -m 644 "$work/key.asc" /usr/share/keyrings/claude-desktop-archive-keyring.asc
source_file=/etc/apt/sources.list.d/claude-desktop.list
backup_once "$source_file"
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main' > "$source_file"
apt-get -o DPkg::Lock::Timeout=180 update || die "APT update failed after configuring the Claude stable source; inspect APT output, or retry with --skip-claude."
apt_install claude-desktop || die "claude-desktop package installation failed (possibly unavailable for this architecture); inspect APT output, or retry with --skip-claude."
dpkg-query -W -f='Claude Desktop ${Version}\n' claude-desktop
