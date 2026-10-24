#!/usr/bin/env python3
"""Phase 4B-1: trusted release sources, signed release manifests, immutable release directories.

Root-only. Python standard library plus the fixed Debian 12 OpenSSH tool
/usr/bin/ssh-keygen (-Y sign/verify, Ed25519). Nothing in this module installs,
switches /opt/webdesktop/app, migrates a database, restarts a service, changes
SSH/firewall/network policy, or deletes a prepared release.

Trust model (see PHASE4B_1_TRUSTED_RELEASE_REPORT.md):
- The repository and the channel -> branch registry are fixed in reviewed code.
- A root administrator enables channels and their Ed25519 signers in
  /etc/webdesktop-release/sources.json (root 0700/0600). Every change is
  appended to a hash-chained audit log. Without that file only the original
  development source (feature/private-deploy-isolation, operator-supplied
  independent SHA256) is accepted, exactly as before 4B-1.
- Any other source must present a canonical, signed release manifest whose
  signer key is pre-registered for that source. A key or branch name shipped
  inside an artifact is never trusted; neither is a CI result by itself.
"""
import argparse
import base64
import contextlib
import ctypes
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('staging_bundle', HERE/'staging_bundle.py')
bundle = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bundle)

INSTALLER_VERSION = 1
REPOSITORY = 'github.com/isAliceYoung/fa'
LEGACY_BRANCH = 'feature/private-deploy-isolation'
NAMESPACE = 'webdesktop-release'
SSH_KEYGEN = '/usr/bin/ssh-keygen'
PLATFORM = dict(os='debian12', arch='amd64', node=bundle.NODE, node_modules_abi='127', glibc='2.36')
# Reviewed registry: root may enable/disable these, never invent another
# repository, channel or branch without a code change.
CHANNELS = {
    'development-legacy': dict(branch=LEGACY_BRANCH, independent_digest=True),
    'testing-cc': dict(branch='feature/CC', independent_digest=False),
}
LEGACY_CHANNEL = 'development-legacy'

# Fixed root-controlled locations. Tests replace these module globals; there is
# no command-line or environment override.
POLICY_DIR = Path('/etc/webdesktop-release')
DEPLOY = Path('/opt/webdesktop')
RELEASES = DEPLOY/'releases'
APP = DEPLOY/'app'
STATE_DIR = Path('/var/lib/webdesktop-release')
LOCK = Path('/run/lock/webdesktop-release.lock')
DEPLOYMENT_STATES = (Path('/etc/webdesktop-staging'), Path('/etc/webdesktop-isolated'))
ROOT_UID = 0
# Tests only: ancestors of their temporary root (never set at runtime).
TRUSTED_ANCESTORS = frozenset()
SERVICE_USER = 'wdsvc'

RESERVE = 768*1024**2
MAX_POLICY = 64*1024
MAX_RELEASE = 16*1024
MAX_SIGNATURE = 8*1024
PRINCIPAL = re.compile(r'[a-z0-9][a-z0-9._-]{2,63}')
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
CREATED = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')
SOURCE_KEYS = {'repository', 'channel', 'branch', 'trust', 'signer', 'signer_fingerprint', 'release_sha256'}


def require(value, message='Release trust check failed'):
    # Runtime validation must survive python -O/-OO.
    if not value:
        raise ValueError(message)


def is_int(value, low, high):
    return type(value) is int and low <= value <= high


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- canonical JSON
def canonical(obj):
    """The ONE encoding that is signed and compared: sorted keys, no spaces, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def strict_json(data, limit):
    """Parse bytes that must already be in canonical form (no reordering, dupes, floats)."""
    require(isinstance(data, bytes) and 0 < len(data) <= limit, 'JSON document size outside bounds')

    def pairs(items):
        keys = [k for k, _ in items]
        require(len(keys) == len(set(keys)), 'Duplicate JSON key')
        return dict(items)

    def reject(_):
        raise ValueError('Only integer JSON numbers are accepted')
    try:
        obj = json.loads(data.decode('ascii'), object_pairs_hook=pairs, parse_float=reject, parse_constant=reject)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Invalid JSON document')
    require(canonical(obj) == data, 'JSON is not in the canonical signed encoding')
    return obj


# ---------------------------------------------------------------- protected files
def reject_acl(path):
    bundle.reject_acl(path)


def protected(path, mode, directory=False, gid=None):
    """Root-owned (or exact group), exact mode, not a link, one link, no ACL."""
    p = Path(path); s = p.lstat()
    require(not stat.S_ISLNK(s.st_mode), 'Protected path is a symbolic link: '+str(p))
    require(stat.S_ISDIR(s.st_mode) if directory else (stat.S_ISREG(s.st_mode) and s.st_nlink == 1), 'Unexpected protected path type: '+str(p))
    require(s.st_uid == ROOT_UID and stat.S_IMODE(s.st_mode) == mode, 'Unexpected protected owner/mode: '+str(p))
    require(gid is None or s.st_gid == gid, 'Unexpected protected group: '+str(p))
    reject_acl(p)
    return s


def protected_path(path):
    """Administrator input (artifact, release.json): absolute, root 0600, safe parents."""
    p = Path(path)
    require(p.is_absolute() and '..' not in p.parts, 'Release input requires an absolute protected path')
    protected(p, 0o600)
    for parent in p.parents:
        if parent in TRUSTED_ANCESTORS:
            continue
        s = parent.lstat()
        require(not stat.S_ISLNK(s.st_mode) and stat.S_ISDIR(s.st_mode) and s.st_uid == ROOT_UID and not s.st_mode & 0o022, 'Unsafe release input parent: '+str(parent))
        reject_acl(parent)
    return p


def protected_input(path, limit):
    data = protected_path(path).read_bytes()
    require(len(data) <= limit, 'Release input exceeds its size bound')
    return data


def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_file(path, data, mode=0o600):
    temporary = path.with_name('.'+path.name+'.new')
    require(not os.path.lexists(temporary), 'Interrupted update requires review: '+str(temporary))
    write_new(temporary, data, mode)
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def rename_noreplace(source, target):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(str(source)), -100, os.fsencode(str(target)), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


@contextlib.contextmanager
def exclusive():
    import fcntl
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == ROOT_UID and s.st_nlink == 1 and not s.st_mode & 0o077, 'Unsafe release lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('BUSY: another release operation is running')
        yield
    finally:
        os.close(fd)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def actor():
    sudo = os.environ.get('SUDO_USER', '')
    try: login = int(Path('/proc/self/loginuid').read_text().strip())
    except (OSError, ValueError): login = -1
    return dict(uid=os.geteuid(), sudo_user=sudo if re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', sudo) else None,
                login_uid=login if 0 <= login < 2**32-1 else None)


# ---------------------------------------------------------------- signer keys
def parse_public_key(text):
    """Exactly one OpenSSH Ed25519 public key. Returns (normalized key, fingerprint)."""
    require(isinstance(text, str) and len(text) <= 1024 and text.count('\n') <= 1, 'Invalid signer key file')
    fields = text.strip().split()
    require(len(fields) in (2, 3) and fields[0] == 'ssh-ed25519', 'Signer must be one ssh-ed25519 public key')
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError:
        raise ValueError('Invalid signer key encoding')
    name = b'ssh-ed25519'
    require(blob == len(name).to_bytes(4, 'big')+name+(32).to_bytes(4, 'big')+blob[-32:] and len(blob) == 51, 'Invalid Ed25519 key blob')
    require(base64.b64encode(blob).decode() == fields[1], 'Non-canonical signer key encoding')
    fingerprint = 'SHA256:'+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return 'ssh-ed25519 '+fields[1], fingerprint


# ---------------------------------------------------------------- source policy
def default_policy():
    return dict(schema=1, repository=REPOSITORY, revision=0, audit_head='0'*64,
                sources=[dict(channel=LEGACY_CHANNEL, branch=LEGACY_BRANCH, independent_digest=True, signers=[])])


def validate_policy(policy):
    require(type(policy) is dict and set(policy) == {'schema', 'repository', 'revision', 'audit_head', 'sources'}, 'Unknown policy fields')
    require(policy['schema'] == 1 and policy['repository'] == REPOSITORY, 'Unsupported policy schema or repository')
    require(is_int(policy['revision'], 0, 10**6) and isinstance(policy['audit_head'], str) and DIGEST.fullmatch(policy['audit_head']), 'Invalid policy revision')
    require(type(policy['sources']) is list and len(policy['sources']) <= len(CHANNELS), 'Invalid source list')
    channels = set(); principals = set()
    for source in policy['sources']:
        require(type(source) is dict and set(source) == {'channel', 'branch', 'independent_digest', 'signers'}, 'Unknown source fields')
        registry = CHANNELS.get(source['channel'])
        require(registry is not None and source['channel'] not in channels, 'Unregistered or duplicate release channel')
        channels.add(source['channel'])
        require(source['branch'] == registry['branch'], 'Channel/branch differs from the reviewed registry')
        require(type(source['independent_digest']) is bool and (registry['independent_digest'] or not source['independent_digest']), 'This channel requires signed release manifests')
        require(type(source['signers']) is list and len(source['signers']) <= 8, 'Invalid signer list')
        fingerprints = set()
        for signer in source['signers']:
            require(type(signer) is dict and set(signer) == {'principal', 'key', 'fingerprint'}, 'Unknown signer fields')
            require(isinstance(signer['principal'], str) and PRINCIPAL.fullmatch(signer['principal']) and signer['principal'] not in principals, 'Invalid or duplicate signer principal')
            principals.add(signer['principal'])
            key, fingerprint = parse_public_key(signer['key'])
            require(key == signer['key'] and fingerprint == signer['fingerprint'] and fingerprint not in fingerprints, 'Signer key/fingerprint mismatch')
            fingerprints.add(fingerprint)
    return policy


def policy_body(policy):
    """What an audit record vouches for: everything except its own revision/head."""
    return sha256(canonical({k: policy[k] for k in ('schema', 'repository', 'sources')}))


def audit_lines(path, last=None):
    data = path.read_bytes()
    require(len(data) <= 4*1024**2 and (not data or data.endswith(b'\n')), 'Invalid audit log')
    previous = '0'*64; count = 0; result = None
    for line in data.splitlines():
        record = strict_json(line, 16*1024)
        require(set(record) == {'seq', 'at', 'actor', 'action', 'detail', 'prev', 'result'} and record['seq'] == count+1 and record['prev'] == previous, 'Audit log chain is broken')
        previous = sha256(line); count += 1; result = record['result']
    if last is not None:
        last.append(result)
    return count, previous


def load_policy():
    """Built-in legacy-only default when nothing was configured; otherwise strict."""
    if not os.path.lexists(POLICY_DIR):
        return dict(default_policy(), explicit=False)
    protected(POLICY_DIR, 0o700, directory=True)
    names = {p.name for p in POLICY_DIR.iterdir()}
    require(names == {'sources.json', 'audit.jsonl'}, 'Unknown or missing release policy files; review '+str(POLICY_DIR))
    protected(POLICY_DIR/'sources.json', 0o600); protected(POLICY_DIR/'audit.jsonl', 0o600)
    policy = validate_policy(strict_json((POLICY_DIR/'sources.json').read_bytes(), MAX_POLICY))
    result = []
    count, head = audit_lines(POLICY_DIR/'audit.jsonl', result)
    require(count == policy['revision'] and head == policy['audit_head'], 'Release policy and its audit log disagree')
    # The latest audit record must vouch for exactly this policy content.
    require(count == 0 and policy_body(policy) == policy_body(default_policy()) or count > 0 and result[0] == policy_body(policy), 'Release policy content differs from its last audited change')
    return dict(policy, explicit=True)


def find_source(policy, channel):
    return next((s for s in policy['sources'] if s['channel'] == channel), None)


def legacy_source(policy):
    source = find_source(policy, LEGACY_CHANNEL)
    require(source is not None and source['independent_digest'], 'The legacy development source is disabled by root policy')
    return dict(repository=REPOSITORY, channel=LEGACY_CHANNEL, branch=LEGACY_BRANCH, trust='independent-digest',
                signer=None, signer_fingerprint=None, release_sha256=None)


# ---------------------------------------------------------------- release manifests
def parse_release(data):
    release = strict_json(data, MAX_RELEASE)
    keys = {'schema', 'type', 'repository', 'channel', 'branch', 'commit', 'artifact', 'unpacked_bytes',
            'inner_manifest_sha256', 'platform', 'installer', 'signer', 'created'}
    require(type(release) is dict and set(release) == keys, 'Unknown or missing release manifest fields')
    require(release['schema'] == 1 and release['type'] == 'webdesktop-release', 'Unsupported release manifest')
    require(release['repository'] == REPOSITORY, 'Unregistered repository')
    registry = CHANNELS.get(release['channel'])
    require(registry is not None, 'Unregistered release channel')
    require(release['branch'] == registry['branch'], 'Release branch is not registered for this channel')
    require(isinstance(release['commit'], str) and SHA.fullmatch(release['commit']), 'Full lowercase 40-character commit SHA required')
    artifact = release['artifact']
    require(type(artifact) is dict and set(artifact) == {'name', 'size', 'sha256'}, 'Invalid artifact description')
    require(isinstance(artifact['name'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.tar\.gz', artifact['name']), 'Invalid artifact name')
    require(is_int(artifact['size'], 1, 450*1024**2) and isinstance(artifact['sha256'], str) and DIGEST.fullmatch(artifact['sha256']), 'Invalid artifact size/digest')
    require(is_int(release['unpacked_bytes'], 1, bundle.MAX_BYTES), 'Invalid unpacked size')
    require(isinstance(release['inner_manifest_sha256'], str) and DIGEST.fullmatch(release['inner_manifest_sha256']), 'Invalid inner manifest digest')
    require(release['platform'] == PLATFORM, 'Release platform/Node/ABI/glibc is not compatible with this installer')
    installer = release['installer']
    require(type(installer) is dict and set(installer) == {'min', 'max'} and is_int(installer['min'], 1, 1000) and is_int(installer['max'], 1, 1000), 'Invalid installer range')
    require(installer['min'] <= INSTALLER_VERSION <= installer['max'], 'Release requires a different installer version')
    require(isinstance(release['signer'], str) and PRINCIPAL.fullmatch(release['signer']), 'Invalid signer principal')
    require(isinstance(release['created'], str) and CREATED.fullmatch(release['created']), 'Invalid creation time')
    return release


def verify_signature(data, signature, signer):
    """ssh-keygen -Y verify against ONE pre-registered key; nothing from the artifact."""
    require(isinstance(signature, bytes) and len(signature) <= MAX_SIGNATURE, 'Signature size outside bounds')
    require(signature.startswith(b'-----BEGIN SSH SIGNATURE-----\n') and signature.endswith(b'-----END SSH SIGNATURE-----\n'), 'Not an armored SSH signature')
    with tempfile.TemporaryDirectory(prefix='wd-release-verify-') as temp:
        allowed = Path(temp)/'allowed_signers'; sig = Path(temp)/'release.sig'
        write_new(allowed, (signer['principal']+' namespaces="'+NAMESPACE+'" '+signer['key']+'\n').encode())
        write_new(sig, signature)
        try:
            r = subprocess.run([SSH_KEYGEN, '-Y', 'verify', '-f', str(allowed), '-I', signer['principal'], '-n', NAMESPACE, '-s', str(sig)],
                               input=data, capture_output=True, timeout=30, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Signature verifier unavailable')
    expected = 'Good "'+NAMESPACE+'" signature for '+signer['principal']+' with ED25519 key '+signer['fingerprint']
    require(r.returncode == 0 and r.stdout.decode('ascii', 'replace').strip() == expected, 'Release signature is not valid for the registered signer')


def authorize(policy, data, signature):
    """Signed manifest -> (release, source record). Every field is checked."""
    release = parse_release(data)
    source = find_source(policy, release['channel'])
    require(source is not None, 'Release channel is not enabled by root policy')
    require(source['branch'] == release['branch'], 'Release branch is not enabled by root policy')
    signer = next((s for s in source['signers'] if s['principal'] == release['signer']), None)
    require(signer is not None, 'Release signer is not trusted for this source')
    verify_signature(data, signature, signer)
    return release, dict(repository=REPOSITORY, channel=release['channel'], branch=release['branch'], trust='signed-manifest',
                         signer=signer['principal'], signer_fingerprint=signer['fingerprint'], release_sha256=sha256(data))


def check_artifact(release, artifact, artifact_sha256, commit):
    require(release['commit'] == commit, 'Signed release commit differs from the pinned commit')
    require(release['artifact']['sha256'] == artifact_sha256, 'Signed release digest differs from the independent digest')
    s = Path(artifact).lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_size == release['artifact']['size'], 'Artifact size differs from the signed release')
    require(bundle.digest(artifact) == artifact_sha256, 'Artifact checksum mismatch')


def resolve_install_source(args):
    """Installer entry. No manifest: legacy source exactly as before 4B-1."""
    policy = load_policy()
    if getattr(args, 'release_manifest', None) is None:
        return None, legacy_source(policy)
    data = protected_input(args.release_manifest, MAX_RELEASE)
    signature = protected_input(Path(str(args.release_manifest)+'.sig'), MAX_SIGNATURE)
    release, source = authorize(policy, data, signature)
    check_artifact(release, args.artifact, args.artifact_sha256, args.commit)
    return dict(release, _bytes=data, _signature=signature), source


def authorized_state(state, state_dir):
    """Re-prove an installed deployment's source against the CURRENT root policy."""
    policy = load_policy()
    source = state.get('release_source')
    if source is None:
        require(state.get('branch') == LEGACY_BRANCH, 'Installed branch has no recorded trusted source')
        legacy_source(policy)
        return
    require(type(source) is dict and set(source) == SOURCE_KEYS and source['trust'] == 'signed-manifest', 'Invalid recorded release source')
    require(source['branch'] == state.get('branch'), 'Recorded source branch differs')
    for name in ('release.json', 'release.json.sig'):
        protected(Path(state_dir)/name, 0o600)
    data = (Path(state_dir)/'release.json').read_bytes()
    signature = (Path(state_dir)/'release.json.sig').read_bytes()
    release, current = authorize(policy, data, signature)
    require(current == source, 'Installed release source is no longer authorized as recorded')
    require(release['commit'] == state.get('commit') and release['artifact']['sha256'] == state.get('artifact_sha256'), 'Recorded release differs from the installed commit/artifact')


# ---------------------------------------------------------------- policy administration
def root_only():
    require(hasattr(os, 'geteuid') and os.geteuid() == ROOT_UID, 'root required')


def installed_states():
    states = []
    for directory in DEPLOYMENT_STATES:
        if os.path.lexists(directory):
            protected(directory, 0o700, directory=True)
            protected(directory/'state.json', 0o600)
            states.append(json.loads((directory/'state.json').read_text()))
    return states


def channels_in_use():
    used = {}
    for state in installed_states():
        source = state.get('release_source')
        used.setdefault(source['channel'] if source else LEGACY_CHANNEL, set()).add(source['signer'] if source else None)
    return used


def commit_policy(policy, action, detail):
    audit = POLICY_DIR/'audit.jsonl'
    count, head = audit_lines(audit)
    require(count == policy['revision'] and head == policy['audit_head'], 'Release policy and its audit log disagree')
    updated = validate_policy({k: v for k, v in policy.items() if k != 'explicit'})
    record = canonical(dict(seq=count+1, at=now(), actor=actor(), action=action, detail=detail, prev=head, result=policy_body(updated)))
    fd = os.open(audit, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        os.write(fd, record+b'\n'); os.fsync(fd)
    finally:
        os.close(fd)
    updated.update(revision=count+1, audit_head=sha256(record))
    replace_file(POLICY_DIR/'sources.json', canonical(validate_policy(updated)))


def read_key_file(path):
    p = Path(path)
    require(p.is_absolute(), 'Signer key file requires an absolute path')
    s = p.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == ROOT_UID and not s.st_mode & 0o022 and s.st_size <= 1024, 'Signer key file must be a root-owned, non-writable regular file')
    reject_acl(p)
    return parse_public_key(p.read_text('ascii'))


def sources_command(a):
    root_only()
    if a.op == 'show':
        policy = load_policy()
        print(json.dumps(public_policy(policy), indent=2)); return
    with exclusive():
        if a.op == 'init':
            require(not os.path.lexists(POLICY_DIR), 'Release policy already exists; it is never re-initialized')
            os.mkdir(POLICY_DIR, 0o700); os.chmod(POLICY_DIR, 0o700)
            write_new(POLICY_DIR/'audit.jsonl', b'')
            write_new(POLICY_DIR/'sources.json', canonical(default_policy()))
            policy = load_policy()
            commit_policy(policy, 'init', dict(sources=[LEGACY_CHANNEL]))
            print('Initialized: only '+LEGACY_CHANNEL+' ('+LEGACY_BRANCH+', independent digest) is trusted.'); return
        policy = load_policy()
        require(policy['explicit'], 'No release policy; run "sources init" first')
        sources = [dict(s, signers=list(s['signers'])) for s in policy['sources']]
        if a.op == 'add-source':
            require(a.channel in CHANNELS and find_source(policy, a.channel) is None, 'Unregistered or already enabled channel')
            source = dict(channel=a.channel, branch=CHANNELS[a.channel]['branch'], independent_digest=False, signers=[])
            if a.signer_key_file:
                require(a.signer_principal is not None, '--signer-principal required with a key')
                key, fingerprint = read_key_file(a.signer_key_file)
                source['signers'].append(dict(principal=a.signer_principal, key=key, fingerprint=fingerprint))
            sources.append(source)
            detail = dict(channel=a.channel, branch=source['branch'], signers=[(s['principal'], s['fingerprint']) for s in source['signers']])
        elif a.op == 'add-signer':
            source = next((s for s in sources if s['channel'] == a.channel), None)
            require(source is not None and a.signer_principal and a.signer_key_file, 'Enabled channel, principal and key file required')
            key, fingerprint = read_key_file(a.signer_key_file)
            source['signers'].append(dict(principal=a.signer_principal, key=key, fingerprint=fingerprint))
            detail = dict(channel=a.channel, principal=a.signer_principal, fingerprint=fingerprint)
        elif a.op == 'remove-signer':
            source = next((s for s in sources if s['channel'] == a.channel), None)
            require(source is not None and any(s['principal'] == a.signer_principal for s in source['signers']), 'Unknown signer')
            require(a.signer_principal not in channels_in_use().get(a.channel, set()), 'The installed deployment depends on this signer; refusing')
            source['signers'] = [s for s in source['signers'] if s['principal'] != a.signer_principal]
            detail = dict(channel=a.channel, principal=a.signer_principal)
        elif a.op == 'remove-source':
            require(find_source(policy, a.channel) is not None, 'Channel is not enabled')
            require(a.channel not in channels_in_use(), 'The installed deployment depends on this source; refusing')
            sources = [s for s in sources if s['channel'] != a.channel]
            detail = dict(channel=a.channel)
        else:
            raise ValueError('Unknown sources operation')
        policy['sources'] = sorted(sources, key=lambda s: s['channel'])
        commit_policy(policy, a.op, detail)
        print('Release policy revision '+str(policy['revision']+1)+' recorded.')


def public_policy(policy):
    return dict(explicit=policy['explicit'], revision=policy['revision'], repository=policy['repository'],
                sources=[dict(channel=s['channel'], branch=s['branch'], independent_digest=s['independent_digest'],
                              signers=[dict(principal=x['principal'], fingerprint=x['fingerprint']) for x in s['signers']]) for s in policy['sources']])


# ---------------------------------------------------------------- signing (CI / release process)
def sign_command(a):
    """Produce release.json + .sig with a key the caller controls. Never on a VPS."""
    artifact = Path(a.artifact)
    with tarfile.open(artifact, 'r:gz') as archive:
        members = archive.getmembers()
        raw = archive.extractfile(archive.getmember('manifest.json')).read()
        unpacked = sum(m.size for m in members)
    inner = json.loads(raw)
    require(inner.get('commit') == a.commit and inner.get('branch') == CHANNELS[a.channel]['branch'], 'Artifact commit/branch differs from the requested release')
    require({k: inner.get(k) for k in PLATFORM} == PLATFORM, 'Artifact platform differs')
    release = dict(schema=1, type='webdesktop-release', repository=REPOSITORY, channel=a.channel, branch=CHANNELS[a.channel]['branch'],
                   commit=a.commit, artifact=dict(name=a.name, size=artifact.stat().st_size, sha256=bundle.digest(artifact)),
                   unpacked_bytes=unpacked, inner_manifest_sha256=sha256(raw), platform=PLATFORM,
                   installer=dict(min=INSTALLER_VERSION, max=INSTALLER_VERSION), signer=a.principal, created=now())
    data = canonical(release); parse_release(data)
    output = Path(a.output)
    write_new(output, data, 0o600)
    r = subprocess.run([SSH_KEYGEN, '-q', '-Y', 'sign', '-f', str(a.key), '-n', NAMESPACE, str(output)], capture_output=True, timeout=30)
    require(r.returncode == 0 and Path(str(output)+'.sig').is_file(), 'Signing failed')
    os.chmod(str(output)+'.sig', 0o600)
    print(json.dumps(dict(commit=a.commit, channel=a.channel, release_sha256=sha256(data))))


# ---------------------------------------------------------------- prepared releases
def service_gid():
    import pwd
    return pwd.getpwnam(SERVICE_USER).pw_gid


def release_state():
    path = STATE_DIR/'state.json'
    if not os.path.lexists(path):
        return dict(schema=1, releases={})
    protected(path, 0o600)
    state = json.loads(path.read_text())
    require(type(state) is dict and state.get('schema') == 1 and type(state.get('releases')) is dict, 'Invalid release state')
    for commit, entry in state['releases'].items():
        require(SHA.fullmatch(commit) and type(entry) is dict and entry.get('state') in ('staging', 'prepared'), 'Invalid release state entry')
    return state


def save_release_state(state):
    replace_file(STATE_DIR/'state.json', json.dumps(state, sort_keys=True, indent=2).encode())


def append_release_audit(action, detail):
    path = STATE_DIR/'audit.jsonl'
    if not os.path.lexists(path):
        write_new(path, b'')
    protected(path, 0o600)
    count, head = audit_lines(path)
    record = canonical(dict(seq=count+1, at=now(), actor=actor(), action=action, detail=detail, prev=head, result=None))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        os.write(fd, record+b'\n'); os.fsync(fd)
    finally:
        os.close(fd)


def ensure_dir(path, mode, gid=None):
    if not os.path.lexists(path):
        os.mkdir(path, mode)
        os.chown(path, ROOT_UID, gid if gid is not None else -1); os.chmod(path, mode)
    protected(path, mode, directory=True, gid=gid)


def installed_deployment():
    present = [d for d in DEPLOYMENT_STATES if os.path.lexists(d)]
    require(len(present) == 1, 'Exactly one installed staging/isolated deployment required')
    directory = present[0]; state = installed_states()[0]
    require(state.get('status') == 'ready' and SHA.fullmatch(state.get('commit', '')), 'The installed deployment is not ready')
    authorized_state(state, directory)
    return directory, state


def release_entries():
    """Classify children of RELEASES. Only recorded 40-hex directories are candidates."""
    if not os.path.lexists(RELEASES):
        return {}, [], []
    names = sorted(p.name for p in RELEASES.iterdir())
    stages = [n for n in names if n.startswith('.stage-')]
    candidates = {n: RELEASES/n for n in names if SHA.fullmatch(n)}
    unknown = [n for n in names if n not in candidates and n not in stages]
    return candidates, stages, unknown


def set_release_permissions(directory, manifest, gid):
    for name, item in manifest['files'].items():
        if not name.startswith('app/') or item['type'] == 'symlink':
            continue
        p = directory/name
        os.chown(p, ROOT_UID, gid, follow_symlinks=False)
        os.chmod(p, 0o750 if item.get('mode', 0) & 0o111 else 0o640)
    for base, dirs, files in os.walk(directory/'app', followlinks=False):
        os.chown(base, ROOT_UID, gid); os.chmod(base, 0o750)


def prepare_command(a):
    root_only()
    _, deployment = installed_deployment()
    policy = load_policy()
    data = protected_input(a.release_manifest, MAX_RELEASE)
    signature = protected_input(Path(str(a.release_manifest)+'.sig'), MAX_SIGNATURE)
    release, source = authorize(policy, data, signature)
    protected_path(a.artifact)
    check_artifact(release, a.artifact, a.artifact_sha256, release['commit'])
    commit = release['commit']; gid = service_gid()
    with exclusive():
        protected(DEPLOY, 0o750, directory=True, gid=gid)
        ensure_dir(RELEASES, 0o750, gid)
        ensure_dir(STATE_DIR, 0o700); ensure_dir(STATE_DIR/'bundles', 0o700)
        candidates, stages, unknown = release_entries()
        require(not stages, 'STALE: an interrupted preparation is retained for review (discard-incomplete)')
        require(not unknown, 'Unknown entries in '+str(RELEASES)+'; review before preparing')
        require(commit not in candidates and not os.path.lexists(RELEASES/commit), 'Release directory exists; immutable versions are never overwritten')
        state = release_state()
        require(all(state['releases'].get(n, {}).get('state') == 'prepared' for n in candidates), 'Unrecorded release entry in '+str(RELEASES)+'; review before preparing')
        require(commit not in state['releases'], 'Release already recorded')
        free = shutil.disk_usage(RELEASES).free
        need = release['unpacked_bytes']+RESERVE
        require(free >= need, 'DISK_SPACE: '+str(need)+' bytes required (unpacked release + reserve), '+str(free)+' available; nothing changed')
        stage = Path(tempfile.mkdtemp(prefix='.stage-'+commit+'-', dir=RELEASES))
        state['releases'][commit] = dict(state='staging', stage=stage.name, channel=source['channel'], branch=source['branch'], started=now())
        save_release_state(state)
        append_release_audit('prepare-start', dict(commit=commit, channel=source['channel'], stage=stage.name))
        payload = stage/'payload'
        manifest = bundle.extract(a.artifact, a.artifact_sha256, commit, payload, branch=source['branch'], inner_sha256=release['inner_manifest_sha256'])
        raw = (payload/'manifest.json').read_bytes()
        require(sha256(raw) == release['inner_manifest_sha256'], 'Inner manifest differs from the signed release')
        require(sum(item.get('size', 0) for item in manifest['files'].values())+len(raw) <= release['unpacked_bytes'], 'Unpacked size exceeds the signed release')
        source_bundle = STATE_DIR/'bundles'/(commit+'.bundle')
        require(not os.path.lexists(source_bundle), 'Source bundle already exists; review')
        os.rename(payload/'source.bundle', source_bundle); os.chmod(source_bundle, 0o600)
        require(bundle.digest(source_bundle) == manifest['files']['source.bundle']['sha256'], 'Source bundle differs')
        os.rename(payload/'app', stage/'app'); os.rename(payload/'manifest.json', stage/'manifest.json'); payload.rmdir()
        write_new(stage/'release.json', data, 0o640); write_new(stage/'release.json.sig', signature, 0o640)
        set_release_permissions(stage, manifest, gid)
        for name in ('manifest.json', 'release.json', 'release.json.sig'):
            os.chown(stage/name, ROOT_UID, gid); os.chmod(stage/name, 0o640)
        os.chown(stage, ROOT_UID, gid); os.chmod(stage, 0o750)
        bundle.verify_tree(stage/'app', manifest, gid)
        rename_noreplace(stage, RELEASES/commit)
        state = release_state()
        state['releases'][commit] = dict(state='prepared', channel=source['channel'], branch=source['branch'], signer=source['signer'],
                                         signer_fingerprint=source['signer_fingerprint'], release_sha256=source['release_sha256'],
                                         artifact_sha256=a.artifact_sha256, inner_manifest_sha256=release['inner_manifest_sha256'], prepared=now())
        save_release_state(state)
        append_release_audit('prepare-done', dict(commit=commit, channel=source['channel']))
    summary = verify_prepared(commit)
    print(json.dumps(dict(prepared=summary, installed_commit=deployment['commit'], switched=False), indent=2))


def verify_prepared(commit, policy=None):
    """A prepared release is recognized only if recorded, signed, authorized and byte-exact."""
    require(isinstance(commit, str) and SHA.fullmatch(commit), 'Full lowercase 40-character commit SHA required')
    policy = policy or load_policy(); gid = service_gid()
    entry = release_state()['releases'].get(commit)
    require(entry is not None and entry['state'] == 'prepared', 'Release is not recorded as prepared')
    protected(RELEASES, 0o750, directory=True, gid=gid)
    directory = RELEASES/commit
    protected(directory, 0o750, directory=True, gid=gid)
    require({p.name for p in directory.iterdir()} == {'app', 'manifest.json', 'release.json', 'release.json.sig'}, 'Unknown/missing release metadata entry')
    for name in ('manifest.json', 'release.json', 'release.json.sig'):
        protected(directory/name, 0o640, gid=gid)
    data = (directory/'release.json').read_bytes()
    release, source = authorize(policy, data, (directory/'release.json.sig').read_bytes())
    require(release['commit'] == commit and source['release_sha256'] == entry['release_sha256'] and source['channel'] == entry['channel'], 'Prepared release differs from its record')
    raw = (directory/'manifest.json').read_bytes()
    require(sha256(raw) == release['inner_manifest_sha256'], 'Prepared inner manifest differs from the signed release')
    manifest = json.loads(raw)
    for key, value in dict(schema=1, commit=commit, branch=release['branch'], **PLATFORM).items():
        require(manifest.get(key) == value, 'Prepared manifest mismatch: '+key)
    bundle.verify_tree(directory/'app', manifest, gid)
    source_bundle = STATE_DIR/'bundles'/(commit+'.bundle')
    protected(source_bundle, 0o600)
    require(bundle.digest(source_bundle) == manifest['files']['source.bundle']['sha256'], 'Source bundle differs')
    return dict(commit=commit, channel=source['channel'], branch=source['branch'], signer=source['signer'],
                signer_fingerprint=source['signer_fingerprint'], verified=True)


def discard_incomplete(a):
    """Explicit administrator removal of OUR recorded, never-published staging tree only."""
    root_only()
    require(SHA.fullmatch(a.commit or ''), 'Full commit required')
    with exclusive():
        state = release_state(); entry = state['releases'].get(a.commit)
        require(entry is not None and entry['state'] == 'staging', 'No incomplete preparation recorded for this commit')
        stage = RELEASES/entry['stage']
        require(entry['stage'].startswith('.stage-'+a.commit+'-') and '/' not in entry['stage'], 'Invalid recorded stage')
        if os.path.lexists(stage):
            protected(stage, 0o700, directory=True)
            shutil.rmtree(stage)
        bundle_path = STATE_DIR/'bundles'/(a.commit+'.bundle')
        require(not os.path.lexists(RELEASES/a.commit), 'A published directory exists; refusing')
        if os.path.lexists(bundle_path):
            protected(bundle_path, 0o600); bundle_path.unlink()
        del state['releases'][a.commit]
        save_release_state(state)
        append_release_audit('discard-incomplete', dict(commit=a.commit, stage=entry['stage']))
    print('Discarded incomplete preparation '+a.commit+'; prepared releases and the installed version were not touched.')


def app_layout():
    if not os.path.lexists(APP):
        return 'absent'
    s = APP.lstat()
    if stat.S_ISDIR(s.st_mode):
        return 'directory'
    if stat.S_ISLNK(s.st_mode):
        target = os.readlink(APP)
        match = re.fullmatch(r'releases/([0-9a-f]{40})/app', target)
        if s.st_uid == ROOT_UID and match:
            try:
                verify_prepared(match.group(1))
                return 'release-link'
            except (ValueError, OSError, KeyError):
                return 'unknown'
    return 'unknown'


def status_command(a):
    root_only()
    policy = load_policy()
    deployment = None
    for directory in DEPLOYMENT_STATES:
        if os.path.lexists(directory):
            protected(directory, 0o700, directory=True); protected(directory/'state.json', 0o600)
            s = json.loads((directory/'state.json').read_text()); src = s.get('release_source')
            deployment = dict(kind=s.get('deployment_kind', 'staging'), commit=s.get('commit'), branch=s.get('branch'), status=s.get('status'),
                              channel=src['channel'] if src else LEGACY_CHANNEL, trust=src['trust'] if src else 'independent-digest',
                              signer=src['signer'] if src else None)
    candidates, stages, unknown = release_entries()
    recorded = release_state()['releases']
    releases = []
    for commit in sorted(set(candidates) | set(recorded)):
        entry = recorded.get(commit, {})
        item = dict(commit=commit, state=entry.get('state', 'unrecorded'), channel=entry.get('channel'), branch=entry.get('branch'))
        try:
            verify_prepared(commit, policy); item['verified'] = True
        except (ValueError, OSError, KeyError) as error:
            item.update(verified=False, reason=str(error)[:160])
        releases.append(item)
    print(json.dumps(dict(schema=1, installer_version=INSTALLER_VERSION, policy=public_policy(policy), deployment=deployment,
                          app_layout=app_layout(), releases=releases, incomplete=stages, unknown_entries=unknown,
                          switching_supported=False), indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('sources', help='Root release-source policy (audited)')
    s.add_argument('op', choices=('show', 'init', 'add-source', 'add-signer', 'remove-signer', 'remove-source'))
    s.add_argument('--channel', choices=sorted(CHANNELS)); s.add_argument('--signer-principal'); s.add_argument('--signer-key-file')
    v = sub.add_parser('verify', help='Verify a signed release against the root policy (no changes)')
    v.add_argument('--release-manifest', type=Path, required=True); v.add_argument('--artifact', type=Path, required=True); v.add_argument('--artifact-sha256', required=True)
    r = sub.add_parser('prepare', help='Verify and unpack into an immutable /opt/webdesktop/releases/<sha> (no switch)')
    r.add_argument('--release-manifest', type=Path, required=True); r.add_argument('--artifact', type=Path, required=True); r.add_argument('--artifact-sha256', required=True)
    c = sub.add_parser('verify-prepared'); c.add_argument('--commit', required=True)
    d = sub.add_parser('discard-incomplete'); d.add_argument('--commit', required=True)
    sub.add_parser('status', help='Non-secret trust/release status')
    g = sub.add_parser('sign', help='CI/release process only: sign an artifact with a caller-held key')
    g.add_argument('--artifact', type=Path, required=True); g.add_argument('--commit', required=True); g.add_argument('--channel', choices=sorted(CHANNELS), required=True)
    g.add_argument('--principal', required=True); g.add_argument('--key', type=Path, required=True); g.add_argument('--output', type=Path, required=True)
    g.add_argument('--name', default='staging-runtime.tar.gz')
    a = p.parse_args(argv)
    os.umask(0o077)
    if a.command == 'sources':
        sources_command(a)
    elif a.command == 'verify':
        root_only()
        data = protected_input(a.release_manifest, MAX_RELEASE)
        release, source = authorize(load_policy(), data, protected_input(Path(str(a.release_manifest)+'.sig'), MAX_SIGNATURE))
        check_artifact(release, a.artifact, a.artifact_sha256, release['commit'])
        print(json.dumps(dict(verified=True, commit=release['commit'], **{k: source[k] for k in ('channel', 'branch', 'signer', 'signer_fingerprint')}), indent=2))
    elif a.command == 'prepare':
        prepare_command(a)
    elif a.command == 'verify-prepared':
        root_only(); print(json.dumps(verify_prepared(a.commit), indent=2))
    elif a.command == 'discard-incomplete':
        discard_incomplete(a)
    elif a.command == 'status':
        status_command(a)
    else:
        sign_command(a)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, tarfile.TarError) as error:
        # Fixed refusal text only; never command output, keys or environment.
        print('Release operation refused: '+type(error).__name__+'; '+str(error)[:300], file=sys.stderr)
        raise SystemExit(1)
