#!/usr/bin/env bash
# Disposable GitHub Ubuntu runner tools only. Never a VPS installer.
set -euo pipefail
ci_vm_packages=(qemu-system-x86 qemu-utils genisoimage)
for ci_vm_option in "$@"; do
  case "$ci_vm_option" in
    --fonts) ci_vm_packages+=(fonts-noto-cjk) ;;
    --git) ci_vm_packages+=(git) ;;
    *) echo 'Unknown CI host-tool option' >&2; exit 64 ;;
  esac
done
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_OS:-}" != Linux ]]; then
  echo 'Disposable GitHub Linux runner only; no package operation performed' >&2
  exit 64
fi
. /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 24.04 || "$(uname -m)" != x86_64 ]]; then
  echo 'Only the pinned Ubuntu 24.04 amd64 CI host is supported' >&2
  exit 64
fi
test -r /usr/share/keyrings/ubuntu-archive-keyring.gpg
ci_vm_apt="$(mktemp -d /tmp/wd-ci-apt.XXXXXXXX)"
chmod 755 "$ci_vm_apt"
mkdir "$ci_vm_apt/sourceparts" "$ci_vm_apt/lists"
sudo install -d -o _apt -g root -m 700 "$ci_vm_apt/lists/partial"
cat > "$ci_vm_apt/sources.list" <<'SOURCES'
deb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://archive.ubuntu.com/ubuntu noble main universe
deb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://archive.ubuntu.com/ubuntu noble-updates main universe
deb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://security.ubuntu.com/ubuntu noble-security main universe
SOURCES
ci_vm_apt_options=(-o "Dir::Etc::sourcelist=$ci_vm_apt/sources.list" -o "Dir::Etc::sourceparts=$ci_vm_apt/sourceparts" -o "Dir::State::lists=$ci_vm_apt/lists" -o APT::Get::List-Cleanup=0 -o Acquire::ForceIPv4=true -o Acquire::Retries=1 -o Acquire::https::Timeout=30)
echo "CI host tools: signed official HTTPS sources; isolated indexes at $ci_vm_apt"
sudo apt-get "${ci_vm_apt_options[@]}" update
sudo apt-get "${ci_vm_apt_options[@]}" install -y --no-install-recommends "${ci_vm_packages[@]}"
if [[ -e /dev/kvm ]]; then sudo chmod a+rw /dev/kvm; fi
# Retain only this newly-created public package metadata until runner disposal;
# do not rewrite /etc/apt or recursively clean any unknown host data.
