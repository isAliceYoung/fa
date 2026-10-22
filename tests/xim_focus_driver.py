"""Compare fixed R1/D1 runtime in the same original 1 GiB no-swap lab."""
import json
from pathlib import Path
import subprocess
import tarfile
import importlib.util

PINNED={
 '5c9ceecfb3e8b07b449ac55c9080c16f0a9b7a76':'0acbfe2664314a4565a2554fe2ebc7959e4e384cd805339de7f5317fce971a64',
 'f30bd8036330911a77c2720ae733960cbb348e16':'91ccd1043bae48469f4b1ca839e659ecbaa81dd71638baea60204c63f444b80e',
}
def run(root,out,work,remote,commit):
    if commit not in PINNED:raise RuntimeError('Only pinned disposable comparison artifacts')
    spec=importlib.util.spec_from_file_location('bundle',root/'webdesktop-deploy/scripts/staging_bundle.py');bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
    payload=work/'verified';bundle.extract(root/'.test-results/staging-runtime.tar.gz',PINNED[commit],commit,payload)
    source=work/'fixed-source';subprocess.run(['git','clone',str(payload/'source.bundle'),str(source)],check=True)
    subprocess.run(['git','-C',str(source),'checkout','--detach',commit],check=True)
    with tarfile.open(work/'xim-lab.tar','w') as archive:
        for name in ('guest-runtime','pty-runtime','gui-runtime','webdesktop-deploy/scripts','webdesktop-deploy/templates'):
            archive.add(source/name,arcname=name,filter=lambda info:None if '__pycache__' in info.name else info)
        archive.add(root/'tests',arcname='tests',filter=lambda info:None if '__pycache__' in info.name else info)
    with (work/'xim-lab.tar').open('rb') as f:remote('sudo mkdir /root/wd-lab && sudo tar -xf - -C /root/wd-lab',stdin=f)
    remote('sudo bash /root/wd-lab/tests/debian_systemd_vm.sh prepare')
    with tarfile.open(work/'app.tar','w') as archive:
        for p in (payload/'app').iterdir():archive.add(p,arcname=p.name)
    with (work/'app.tar').open('rb') as f:remote('sudo tar -xf - -C /opt/webdesktop',stdin=f,timeout=180)
    remote('sudo bash /root/wd-lab/tests/debian_pty_vm.sh',timeout=300)
    remote('sudo bash /root/wd-lab/tests/debian_gui_vm.sh',timeout=300)
    remote('sudo bash /root/wd-lab/tests/prepare_desktop.sh',timeout=300)
    env=remote("sudo sh -c 'cat /proc/swaps; uname -r; dpkg-query -W chromium fcitx5 xterm libx11-6; systemctl show webdesktop.slice -p MemoryMax -p MemorySwapMax -p CPUQuotaPerSecUSec; sha256sum /usr/local/lib/webdesktop-gui/*.py'",capture_output=True,text=True).stdout
    (out/'xim-runtime-provenance.json').write_text(json.dumps(dict(runtime_commit=commit,artifact_sha256=PINNED[commit],controller_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),environment=env),indent=2))
    remote('sudo /usr/local/bin/node /root/wd-lab/tests/xim_diagnostic.mjs --standalone',timeout=180)
    # Exactly the original ordered pressure + GTK/Qt restart -> XIM suite,
    # without Claude packages or additional memory feasibility experiments.
    code=0
    try:remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_desktop_runtime.mjs',timeout=720)
    except subprocess.CalledProcessError as error:code=error.returncode
    (out/'xim-sequence-exit.json').write_text(json.dumps(dict(exit=code)))
