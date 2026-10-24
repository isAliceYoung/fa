#!/usr/bin/env bash
# Shared authorization, not a bypass of disposable-lab guards.
staging_authorized() {
  deployment_authorized staging
}

deployment_authorized() {
  python3 -I - "${1:-runtime}" /opt/webdesktop/source/webdesktop-deploy/scripts/release_trust.py <<'PY'
import json,os,re,stat
from pathlib import Path
import sys
def require(ok):
 if not ok: raise SystemExit('Invalid deployment authorization')
kind=sys.argv[1]
if kind=='runtime':
 existing=[k for k in ('staging','isolated') if Path('/etc/webdesktop-'+k).exists()]
 require(len(existing)==1);kind=existing[0]
require(kind in ('staging','isolated'))
p=Path('/etc/webdesktop-'+kind)
require(os.geteuid()==0 and p.lstat().st_uid==0 and stat.S_IMODE(p.lstat().st_mode)==0o700 and not p.is_symlink())
f=p/'state.json'; s=f.lstat()
require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600)
r=json.loads(f.read_text())
require(r['schema']==1 and r['authorized_new_vps'] is True)
import importlib.util
# The installed root-only source checkout (also used by recovery); never the
# minimal guest runtime copy of this script. Refuse an unsafe trust module.
trust_path=Path(sys.argv[2])
for node in (trust_path,*trust_path.parents):
 n=node.lstat()
 require(not stat.S_ISLNK(n.st_mode) and n.st_uid==0 and not n.st_mode&0o022)
spec=importlib.util.spec_from_file_location('release_trust',trust_path);trust=importlib.util.module_from_spec(spec);spec.loader.exec_module(trust)
# Legacy branch, or a signed release still authorized by root policy (4B-1).
try: trust.authorized_state(r,p)
except (ValueError,OSError,KeyError): require(False)
require(r.get('deployment_kind','staging')==kind)
require(re.fullmatch('[0-9a-f]{40}',r['commit']) and r['status'] in ('installing','ready'))
print(r['commit'])
PY
}
authorize_layer() {
  [[ $# == 1 && $EUID == 0 ]] || return 64
  . /etc/os-release
  [[ "$ID:$VERSION_ID" == debian:12 ]] || return 64
  layer_mode="$1"
  case "$layer_mode" in
    --lab) ;;
    --staging|--isolated)
      local kind="${layer_mode#--}" commit
      commit="$(deployment_authorized "$kind")" || return 64
      local source_root
      source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")/../.." && pwd -P)"
      [[ "$(git -C "$source_root" rev-parse HEAD)" == "$commit" ]] || return 64
      ;;
    *) return 64 ;;
  esac
}
