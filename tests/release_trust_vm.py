"""Phase 4B-1 root fault injection ONLY in the controller's disposable Debian VM.

Runs against the real isolated feature/CC install (signed source) using the
real CLI entry release.sh. Inputs prepared by staging_vm_driver.py:
  /root/staging-input/{runtime.tar.gz,release.json,release.json.sig}  version A (installed commit)
  /root/release-test/b/{runtime.tar.gz,release.json,release.json.sig} version B (baseline f195f90)
  /root/release-test/legacy-signer.pub, /root/staging-input/neg-signer/{release.json,.sig}
Throwaway test keys only; no production key, VPS or GitHub write.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import sys
import time

if '--disposable-vm' not in sys.argv or os.geteuid() != 0 or socket.gethostname() != 'wd-lab' or not Path('/var/tmp/wd-lab-ready').is_file():
    raise SystemExit('Explicit disposable VM required')
AFTER = '--after-reboot' in sys.argv
SOURCE = Path('/opt/webdesktop/source')
CLI = ['bash', str(SOURCE/'webdesktop-deploy/release.sh')]
SCRIPT = SOURCE/'webdesktop-deploy/scripts/release_trust.py'
STATE = Path('/etc/webdesktop-isolated')
POLICY = Path('/etc/webdesktop-release')
RELEASES = Path('/opt/webdesktop/releases')
RSTATE = Path('/var/lib/webdesktop-release')
APP = Path('/opt/webdesktop/app')
TEST = Path('/root/release-test')
BACKEND = 'webdesktop-isolated.service'
OUT = Path('/root/staging-results')/('release-trust-reboot.json' if AFTER else 'release-trust.json')
installed = json.loads((STATE/'state.json').read_text())
A = installed['commit']
A_RELEASE = Path('/root/staging-input/release.json'); A_ART = Path('/root/staging-input/runtime.tar.gz')
B_RELEASE = TEST/'b/release.json'; B_ART = TEST/'b/runtime.tar.gz'
B = json.loads(B_RELEASE.read_bytes())['commit'] if B_RELEASE.exists() else None
report = dict(schema=1, commit=A, passed=0, failed=0, skipped=0, not_executed=0, tests=[], resources={})
PLANNED = (['after reboot: installed signed source and prepared releases still verify'] if AFTER else [
    'root policy is private, audited and unreadable/unwritable by the four service identities',
    'non-root identities cannot change release sources or read status',
    'installed feature/CC deployment re-proves its signed source (installer layer gate)',
    'release status exposes no secrets',
    'prepare A: verified immutable candidate; installed release, credentials and backend untouched',
    'prepared release DAC: wdsvc read-only, guests and bundle access denied',
    'an existing prepared version is never overwritten',
    'untrusted signer and tampered artifact are refused before any write',
    'unknown, unrecorded and symlinked release entries block preparation',
    'insufficient disk space refuses before extraction',
    'interrupted extraction is never recognized; stale stage blocks; explicit discard',
    'concurrent preparation: exactly one runs, the other is BUSY',
    'audited legacy signer addition; two fixed versions coexist and verify independently',
    'tampered prepared release refused and retained (file, dir, ACL, mode, hard link, symlink)',
    'optimized interpreters cannot bypass prepared-release verification',
    'policy tampering fails closed for recovery and layer authorization',
    'isolated recovery unchanged with prepared releases present',
    'release-link app layout recognized but refused by recovery; no conversion',
    'final: installed deployment, credentials, DB row and guest data unchanged'])
report['not_executed'] = len(PLANNED)


def save():
    OUT.write_text(json.dumps(report, indent=2))


def require(ok, message='Release VM assertion failed'):
    if not ok:
        raise ValueError(message)


def run(args, expect=0, env=None, timeout=600):
    r = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=timeout, env=env)
    if expect is not None and r.returncode != expect:
        raise RuntimeError('exit '+str(r.returncode)+' (expected '+str(expect)+') for '+' '.join(map(str, args))[:200]+': '+(r.stdout+r.stderr)[-600:])
    return r


def cli(*args, expect=0, timeout=600):
    return run(CLI+list(args), expect=expect, timeout=timeout)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def tree_sha(path):
    h = hashlib.sha256()
    for directory, dirs, files in sorted(os.walk(path)):
        dirs.sort()
        for name in sorted(files):
            p = Path(directory)/name
            h.update(str(p).encode()); h.update(os.readlink(p).encode() if p.is_symlink() else sha(p).encode())
    return h.hexdigest()


def test(name, fn):
    require(name in PLANNED)
    report['not_executed'] -= 1
    try:
        evidence = fn(); report['passed'] += 1; report['tests'].append(dict(name=name, result='passed', evidence=evidence))
    except Exception as e:
        report['failed'] += 1; report['tests'].append(dict(name=name, result='failed', detail=(type(e).__name__+': '+str(e))[:1500]))
        raise
    finally:
        save()


def access(path, user):
    code = '''import os,sys,json
r=[]
for flags in (os.O_RDONLY,os.O_WRONLY):
 try:fd=os.open(sys.argv[1],flags)
 except OSError:r.append(False)
 else:os.close(fd);r.append(True)
print(json.dumps(r))
'''
    return json.loads(run(['runuser', '-u', user, '--', 'python3', '-I', '-c', code, str(path)]).stdout)


def can_create(directory, user):
    code = 'import os,sys\ntry:fd=os.open(os.path.join(sys.argv[1],"wd-4b1-probe"),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\nexcept OSError:print("no")\nelse:os.close(fd);print("yes")'
    return run(['runuser', '-u', user, '--', 'python3', '-I', '-c', code, str(directory)]).stdout.strip() == 'yes'


def mainpid():
    return run(['systemctl', 'show', BACKEND, '-p', 'MainPID', '--value']).stdout.strip()


def snapshot():
    from urllib.parse import urlsplit
    cfg = dict(l.split('=', 1) for l in (APP/'.env').read_text().splitlines() if '=' in l)
    db = urlsplit(cfg['DATABASE_URL']).path[1:]
    row = run(['runuser', '-u', 'postgres', '--', 'psql', '-At', '-d', db, '-c', 'SELECT count(*) FROM pg_tables']).stdout.strip()
    files = [APP/'.env', APP/'server.mjs', STATE/'login.json', STATE/'state.json', STATE/'recovery.json', STATE/'release.json']
    guest = Path('/home/wdguest/Documents/phase4a-preserve.txt')
    return dict(files={str(p): sha(p) for p in files}, guest=sha(guest) if guest.exists() else None, tables=row,
                volume=run(['blkid', '-s', 'UUID', '-o', 'value', '/var/lib/webdesktop-guest-volume/workspace.ext4']).stdout.strip())


def oom_kills():
    return {line.split()[0]: int(line.split()[1]) for line in Path('/sys/fs/cgroup/webdesktop.slice/memory.events').read_text().splitlines()}


def measured(name, args, expect=0):
    """Run one preparation and sample its real process tree, FDs, memory and disk."""
    before_disk = os.statvfs('/opt'); events = oom_kills(); started = time.monotonic()
    usage = os.times()
    proc = subprocess.Popen([str(x) for x in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak_rss = peak_fds = 0; min_avail = None
    while proc.poll() is None:
        try:
            rss = 0
            for pid in [proc.pid]+[int(x) for x in Path('/proc/'+str(proc.pid)+'/task/'+str(proc.pid)+'/children').read_text().split()]:
                for line in Path('/proc/'+str(pid)+'/status').read_text().splitlines():
                    if line.startswith('VmRSS:'): rss += int(line.split()[1])*1024
            peak_rss = max(peak_rss, rss)
            peak_fds = max(peak_fds, len(os.listdir('/proc/'+str(proc.pid)+'/fd')))
        except (OSError, ValueError):
            pass
        avail = int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable')).split()[1])*1024
        min_avail = avail if min_avail is None else min(min_avail, avail)
        time.sleep(.2)
    out, err = proc.communicate()
    after = os.times()
    require(proc.returncode == expect, name+' exit '+str(proc.returncode)+': '+(out+err)[-600:])
    after_disk = os.statvfs('/opt')
    record = dict(wall_s=round(time.monotonic()-started, 1), cpu_s=round((after.children_user+after.children_system)-(usage.children_user+usage.children_system), 1),
                  peak_rss_mib=round(peak_rss/2**20, 1), peak_fds=peak_fds, min_mem_available_mib=round((min_avail or 0)/2**20),
                  disk_used_mib=round(((before_disk.f_bavail-after_disk.f_bavail)*after_disk.f_frsize)/2**20),
                  oom_kill_delta=oom_kills()['oom_kill']-events['oom_kill'], cpus=os.cpu_count(),
                  mem_total_mib=round(int(Path('/proc/meminfo').read_text().split()[1])/1024))
    report['resources'][name] = record
    require(record['oom_kill_delta'] == 0, 'Preparation caused an OOM kill')
    return out


IDS = ('wdsvc', 'wdguest', 'wdterm', 'wdgui')
initial = snapshot()


def status():
    return json.loads(cli('status').stdout)


try:
    if AFTER:
        def after_reboot():
            cli('verify-prepared', '--commit', A)
            if B: cli('verify-prepared', '--commit', B)
            s = status(); require(s['deployment']['channel'] == 'testing-cc' and s['app_layout'] == 'directory')
            run(['bash', '-c', '. '+str(SOURCE/'webdesktop-deploy/scripts/layer-context.sh')+'; deployment_authorized isolated'])
            return dict(verified=[r['commit'] for r in s['releases'] if r['verified']])
        test(PLANNED[0], after_reboot)
    else:
        def private_policy():
            require(oct(POLICY.stat().st_mode & 0o777) == '0o700')
            for name in ('sources.json', 'audit.jsonl'): require(oct((POLICY/name).stat().st_mode & 0o777) == '0o600')
            result = {}
            for user in IDS:
                result[user] = dict(sources=access(POLICY/'sources.json', user), audit=access(POLICY/'audit.jsonl', user), create=can_create(POLICY, user))
                require(result[user] == dict(sources=[False, False], audit=[False, False], create=False), user)
            lines = (POLICY/'audit.jsonl').read_bytes().splitlines()
            return dict(audit_actions=[json.loads(l)['action'] for l in lines], denied=result)
        test(PLANNED[0], private_policy)

        def non_root():
            before = sha(POLICY/'sources.json')
            pub = TEST/'legacy-signer.pub'
            for user in IDS:
                r = run(['runuser', '-u', user, '--', 'python3', '-I', SCRIPT, 'sources', 'add-signer', '--channel', 'testing-cc', '--signer-principal', 'evil-'+user, '--signer-key-file', pub], expect=None)
                require(r.returncode != 0, user)
                r = run(['runuser', '-u', user, '--', 'python3', '-I', SCRIPT, 'status'], expect=None)
                require(r.returncode != 0, user)
            require(sha(POLICY/'sources.json') == before)
            return dict(refused=list(IDS))
        test(PLANNED[1], non_root)

        def installed_source():
            s = status()['deployment']
            require((s['channel'], s['branch'], s['trust']) == ('testing-cc', 'feature/CC', 'signed-manifest'))
            out = run(['bash', '-c', '. '+str(SOURCE/'webdesktop-deploy/scripts/layer-context.sh')+'; deployment_authorized isolated']).stdout.strip()
            require(out == A)
            return s
        test(PLANNED[2], installed_source)

        def no_secrets():
            text = cli('status').stdout
            cfg = dict(l.split('=', 1) for l in (APP/'.env').read_text().splitlines() if '=' in l)
            login = json.loads((STATE/'login.json').read_text())
            values = [v for v in list(cfg.values())+[str(v) for v in login.values()] if len(v) >= 8]
            require(values and not any(v in text for v in values), 'Secret value in status')
            for marker in ('PRIVATE KEY', 'DATABASE_URL', 'AUTH_KEY', 'PASSWORD'): require(marker not in text, marker)
            return dict(checked_values=len(values))
        test(PLANNED[3], no_secrets)

        def prepare_a():
            pid = mainpid(); before = snapshot()
            measured('prepare-A', CLI+['prepare', '--release-manifest', A_RELEASE, '--artifact', A_ART, '--artifact-sha256', json.loads(A_RELEASE.read_bytes())['artifact']['sha256']])
            require(snapshot() == before and mainpid() == pid and run(['systemctl', 'is-active', BACKEND]).stdout.strip() == 'active')
            require(APP.is_dir() and not APP.is_symlink())
            v = json.loads(cli('verify-prepared', '--commit', A).stdout)
            require(v['verified'] and v['channel'] == 'testing-cc')
            # Same bytes as the installed (independently verified) runtime.
            require(sha(RELEASES/A/'app/server.mjs') == sha(APP/'server.mjs'))
            return dict(verified=v, backend_pid_unchanged=True, resources=report['resources']['prepare-A'])
        test(PLANNED[4], prepare_a)

        def dac():
            f = RELEASES/A/'app/server.mjs'
            result = {u: access(f, u) for u in IDS}
            require(result['wdsvc'] == [True, False])
            for u in IDS[1:]: require(result[u] == [False, False], u)
            for u in IDS: require(not can_create(RELEASES, u) and not can_create(RELEASES/A/'app', u), u)
            bundle = RSTATE/'bundles'/(A+'.bundle')
            for u in IDS: require(access(bundle, u) == [False, False], u)
            return dict(server=result, bundle_denied=list(IDS))
        test(PLANNED[5], dac)

        def immutable():
            before = tree_sha(RELEASES/A)
            r = cli('prepare', '--release-manifest', A_RELEASE, '--artifact', A_ART, '--artifact-sha256', json.loads(A_RELEASE.read_bytes())['artifact']['sha256'], expect=1)
            require('never overwritten' in r.stderr and tree_sha(RELEASES/A) == before)
            return dict(refused=True)
        test(PLANNED[6], immutable)

        b_digest = json.loads(B_RELEASE.read_bytes())['artifact']['sha256']
        prepare_b = CLI+['prepare', '--release-manifest', B_RELEASE, '--artifact', B_ART, '--artifact-sha256', b_digest]

        def untrusted():
            entries = sorted(os.listdir(RELEASES))
            u = Path('/root/staging-input/neg-signer/release.json')
            r = cli('prepare', '--release-manifest', u, '--artifact', A_ART, '--artifact-sha256', json.loads(u.read_bytes())['artifact']['sha256'], expect=1)
            require('signature' in r.stderr or 'signer' in r.stderr, r.stderr)
            # B before its signer is registered: not trusted for the legacy source.
            r2 = run(prepare_b, expect=1); require('signer' in r2.stderr, r2.stderr)
            evil = TEST/'evil.tar.gz'
            run(['cp', A_ART, evil]); run(['chmod', '600', evil])
            with evil.open('ab') as f: f.write(b'\0')
            r3 = cli('prepare', '--release-manifest', A_RELEASE, '--artifact', evil, '--artifact-sha256', sha(evil), expect=1)
            evil.unlink()
            require(sorted(os.listdir(RELEASES)) == entries)
            return dict(untrusted=r.stderr.strip()[-120:], unregistered_signer=r2.stderr.strip()[-120:], tampered=r3.stderr.strip()[-120:])
        test(PLANNED[7], untrusted)
        cli('sources', 'add-signer', '--channel', 'development-legacy', '--signer-principal', 'ci-legacy-signer', '--signer-key-file', TEST/'legacy-signer.pub')

        def entries():
            results = {}
            for name, make, undo in [
                ('unrecorded-hex', lambda: (RELEASES/('0'*40)).mkdir(mode=0o750), lambda: (RELEASES/('0'*40)).rmdir()),
                ('unknown-name', lambda: (RELEASES/'notes').write_text('x'), lambda: (RELEASES/'notes').unlink()),
                ('hex-symlink', lambda: (RELEASES/('1'*40)).symlink_to('/var/tmp'), lambda: (RELEASES/('1'*40)).unlink())]:
                make()
                try:
                    r = run(prepare_b, expect=1); results[name] = r.stderr.strip()[-100:]
                    require(not os.path.lexists(RELEASES/B))
                finally:
                    undo()
            elsewhere = Path('/var/tmp/wd-4b1-releases'); elsewhere.mkdir(mode=0o750)
            RELEASES.rename('/opt/webdesktop/releases.saved'); RELEASES.symlink_to(elsewhere)
            try:
                r = run(prepare_b, expect=1); results['releases-symlink'] = r.stderr.strip()[-100:]
                require(list(elsewhere.iterdir()) == [])
            finally:
                RELEASES.unlink(); Path('/opt/webdesktop/releases.saved').rename(RELEASES); elsewhere.rmdir()
            return results
        test(PLANNED[8], entries)

        def disk():
            need = json.loads(B_RELEASE.read_bytes())['unpacked_bytes']+768*1024**2
            free = os.statvfs('/opt'); free = free.f_bavail*free.f_frsize
            filler = Path('/var/tmp/wd-4b1-filler')
            size = max(0, free-need+200*1024**2)
            run(['fallocate', '-l', str(size), filler])
            try:
                r = run(prepare_b, expect=1)
                require('DISK_SPACE' in r.stderr, r.stderr)
                require(not any(n.startswith('.stage-') for n in os.listdir(RELEASES)) and not os.path.lexists(RELEASES/B))
                return dict(need_mib=need >> 20, free_mib_during=(free-size) >> 20, message=r.stderr.strip()[-120:])
            finally:
                filler.unlink()
        test(PLANNED[9], disk)

        def interrupted():
            proc = subprocess.Popen([str(x) for x in prepare_b], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic()+300; stage = None
            while time.monotonic() < deadline and proc.poll() is None:
                stages = [n for n in os.listdir(RELEASES) if n.startswith('.stage-')]
                if stages and (RELEASES/stages[0]/'payload/app').exists():
                    count = sum(len(f) for _, _, f in os.walk(RELEASES/stages[0]/'payload/app'))
                    if count > 200:
                        stage = stages[0]; os.kill(proc.pid, signal.SIGKILL); break
                time.sleep(.05)
            proc.wait()
            require(stage is not None, 'Did not observe an in-progress extraction to interrupt')
            s = status()
            require(s['incomplete'] == [stage] and not os.path.lexists(RELEASES/B))
            entry = next(r for r in s['releases'] if r['commit'] == B)
            require(entry['state'] == 'staging' and entry['verified'] is False)
            cli('verify-prepared', '--commit', B, expect=1)
            r = run(prepare_b, expect=1); require('STALE' in r.stderr, r.stderr)
            cli('verify-prepared', '--commit', A)
            require(snapshot() == initial)
            cli('discard-incomplete', '--commit', B)
            require(status()['incomplete'] == [])
            return dict(killed_stage=stage, recognized=False)
        test(PLANNED[10], interrupted)

        def concurrent():
            import threading
            second = {}
            def contender():
                # Starts once the measured preparation holds the lock.
                deadline = time.monotonic()+120
                while time.monotonic() < deadline and not any(n.startswith('.stage-') for n in os.listdir(RELEASES)):
                    time.sleep(.05)
                second['r'] = run(prepare_b, expect=None)
            t = threading.Thread(target=contender); t.start()
            measured('prepare-B', prepare_b)
            t.join(timeout=300)
            require(second['r'].returncode == 1 and 'BUSY' in second['r'].stderr, second['r'].stderr)
            return dict(second=second['r'].stderr.strip()[-80:], resources=report['resources']['prepare-B'])
        test(PLANNED[11], concurrent)

        def coexist():
            s = status()
            verified = sorted(r['commit'] for r in s['releases'] if r['verified'])
            require(verified == sorted([A, B]))
            require(sha(RELEASES/A/'app/server.mjs') != sha(RELEASES/B/'app/server.mjs'))
            lines = [json.loads(l) for l in (POLICY/'audit.jsonl').read_bytes().splitlines()]
            require(lines[-1]['action'] == 'add-signer' and lines[-1]['detail']['principal'] == 'ci-legacy-signer')
            return dict(verified=verified, channels={r['commit']: r['channel'] for r in s['releases']})
        test(PLANNED[12], coexist)

        def tampered():
            d = RELEASES/B/'app'; f = d/'server.mjs'; results = {}
            def case(name, apply, undo):
                apply()
                try:
                    r = cli('verify-prepared', '--commit', B, expect=1); results[name] = r.stderr.strip()[-90:]
                finally:
                    undo()
                cli('verify-prepared', '--commit', B)
            extra = d/'r4b1-unknown'
            case('unknown file', lambda: (extra.write_text('x'), os.chown(extra, 0, pwd.getpwnam('wdsvc').pw_gid), extra.chmod(0o640)), extra.unlink)
            case('unknown dir', lambda: (d/'r4b1-dir').mkdir(mode=0o750), lambda: (d/'r4b1-dir').rmdir())
            case('named ACL', lambda: run(['setfacl', '-m', 'u:wdguest:r--', f]), lambda: run(['setfacl', '-b', f]))
            case('default dir ACL', lambda: run(['setfacl', '-m', 'd:u:wdguest:r-x', d]), lambda: run(['setfacl', '-k', d]))
            case('mode drift', lambda: f.chmod(0o644), lambda: f.chmod(0o640))
            link = Path('/opt/webdesktop/r4b1-hardlink')
            case('hard link', lambda: os.link(f, link), link.unlink)
            saved = d/'r4b1-saved'
            case('file replaced by symlink', lambda: (f.rename(saved), f.symlink_to('package.json')), lambda: (f.unlink(), saved.rename(f)))
            nxt = d/'.next'; moved = Path('/opt/webdesktop/r4b1-next')
            case('directory replaced', lambda: (nxt.rename(moved), nxt.mkdir(mode=0o750)), lambda: (nxt.rmdir(), moved.rename(nxt)))
            return results
        test(PLANNED[13], tampered)

        def optimized():
            p = RELEASES/B/'app/r4b1-unknown'; p.write_text('retained'); p.chmod(0o640)
            try:
                for flag in ('-O', '-OO'):
                    r = run(['python3', '-I', flag, SCRIPT, 'verify-prepared', '--commit', B], expect=1)
                    require(p.read_text() == 'retained')
                return dict(optimizations=['-O', '-OO'], refused=True)
            finally:
                p.unlink()
        test(PLANNED[14], optimized)

        def policy_tamper():
            original = (POLICY/'sources.json').read_bytes()
            policy = json.loads(original)
            policy['sources'] = [s for s in policy['sources'] if s['channel'] != 'testing-cc']
            (POLICY/'sources.json').write_bytes(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode())
            try:
                r = run(['bash', str(SOURCE/'webdesktop-deploy/install-isolated.sh'), '--mode', 'service', '--commit', A], expect=1)
                g = run(['bash', '-c', '. '+str(SOURCE/'webdesktop-deploy/scripts/layer-context.sh')+'; deployment_authorized isolated'], expect=None)
                require(g.returncode != 0)
                cli('verify-prepared', '--commit', A, expect=1)
            finally:
                (POLICY/'sources.json').write_bytes(original)
            run(['bash', str(SOURCE/'webdesktop-deploy/install-isolated.sh'), '--mode', 'service', '--commit', A])
            return dict(recovery=r.stdout.strip()[-120:], layer_refused=True)
        test(PLANNED[15], policy_tamper)

        def recovery():
            pid = mainpid(); before = snapshot()
            run(['bash', str(SOURCE/'webdesktop-deploy/install-isolated.sh'), '--mode', 'service', '--commit', A])
            require(snapshot() == before and mainpid() == pid)
            return dict(pid_unchanged=True)
        test(PLANNED[16], recovery)

        def link_layout():
            saved = Path('/opt/webdesktop/app.r4b1-saved')
            run(['systemctl', 'stop', BACKEND])
            APP.rename(saved); APP.symlink_to('releases/'+A+'/app')
            try:
                layout = status()['app_layout']
                require(layout == 'release-link', layout)
                r = run(['bash', str(SOURCE/'webdesktop-deploy/install-isolated.sh'), '--mode', 'service', '--commit', A], expect=1)
                require(APP.is_symlink() and saved.is_dir() and run(['systemctl', 'is-active', BACKEND], expect=None).stdout.strip() != 'active')
            finally:
                if APP.is_symlink(): APP.unlink()
                saved.rename(APP)
            run(['bash', str(SOURCE/'webdesktop-deploy/install-isolated.sh'), '--mode', 'service', '--commit', A])
            require(run(['systemctl', 'is-active', BACKEND]).stdout.strip() == 'active' and status()['app_layout'] == 'directory')
            return dict(recognized='release-link', recovery_refused=r.stdout.strip()[-120:], restored=True)
        test(PLANNED[17], link_layout)

        test(PLANNED[18], lambda: (require(snapshot() == initial), dict(unchanged=True))[1])
finally:
    save()
print(json.dumps({k: report[k] for k in ('passed', 'failed', 'skipped', 'not_executed')}))
