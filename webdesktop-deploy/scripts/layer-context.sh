#!/usr/bin/env bash
# Shared authorization, not a bypass of disposable-lab guards.
staging_authorized() {
  python3 -I - <<'PY'
import json,os,re,stat
from pathlib import Path
p=Path('/etc/webdesktop-staging')
assert os.geteuid()==0 and p.lstat().st_uid==0 and stat.S_IMODE(p.lstat().st_mode)==0o700 and not p.is_symlink()
f=p/'state.json'; s=f.lstat()
assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600
r=json.loads(f.read_text())
assert r['schema']==1 and r['authorized_new_vps'] is True and r['branch']=='feature/private-deploy-isolation'
assert re.fullmatch('[0-9a-f]{40}',r['commit']) and r['status'] in ('installing','ready')
PY
}
authorize_layer() {
  [[ $# == 1 && $EUID == 0 ]] || return 64
  . /etc/os-release
  [[ "$ID:$VERSION_ID" == debian:12 ]] || return 64
  layer_mode="$1"
  case "$layer_mode" in
    --lab) ;;
    --staging)
      staging_authorized || return 64
      local source_root
      source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")/../.." && pwd -P)"
      [[ "$(git -C "$source_root" rev-parse HEAD)" == "$(python3 -I -c 'import json;print(json.load(open("/etc/webdesktop-staging/state.json"))["commit"])')" ]] || return 64
      ;;
    *) return 64 ;;
  esac
}
