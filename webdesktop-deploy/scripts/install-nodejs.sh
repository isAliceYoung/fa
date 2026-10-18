#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian
apt_install curl ca-certificates xz-utils
case "$(dpkg --print-architecture)" in
  amd64) arch=x64 ;;
  arm64) arch=arm64 ;;
  *) die "Only amd64 and arm64 are supported." ;;
esac
destination="/opt/nodejs/v$WD_NODE_VERSION"
if [[ ! -x "$destination/bin/node" ]] || [[ "$("$destination/bin/node" --version)" != "v$WD_NODE_VERSION" ]]; then
  [[ ! -e "$destination" ]] || die "Incomplete or unexpected Node installation at $destination; inspect it before retrying."
  work="$(mktemp -d)"
  trap 'rm -rf -- "$work"' EXIT
  archive="node-v$WD_NODE_VERSION-linux-$arch.tar.xz"
  base="https://nodejs.org/dist/v$WD_NODE_VERSION"
  curl --fail --show-error --location --retry 3 -o "$work/$archive" "$base/$archive" || \
    die "Node.js v$WD_NODE_VERSION ($arch) download failed: $base/$archive. Check outbound HTTPS, TLS certificates and free disk space."
  curl --fail --show-error --location --retry 3 -o "$work/SHASUMS256.txt" "$base/SHASUMS256.txt" || \
    die "Node.js checksum download failed: $base/SHASUMS256.txt. Check outbound HTTPS and TLS certificates."
  # Select exactly this artifact; never silently accept an absent checksum.
  awk -v name="$archive" '$2 == name {print}' "$work/SHASUMS256.txt" > "$work/selected.sha256"
  [[ "$(wc -l < "$work/selected.sha256")" == 1 ]] || die "Node artifact checksum was not found."
  (cd "$work"; sha256sum --check selected.sha256)
  install -d -m 755 /opt/nodejs
  tar -xJf "$work/$archive" -C "$work"
  mv -- "$work/node-v$WD_NODE_VERSION-linux-$arch" "$destination"
fi
for bin in node npm npx; do
  link="/usr/local/bin/$bin"
  if [[ -e "$link" && ! -L "$link" ]]; then
    log "Keeping existing $link; service and installer use $destination/bin explicitly."
  else
    ln -sfn "$destination/bin/$bin" "$link"
  fi
done
"$destination/bin/node" --version
"$destination/bin/npm" --version
