"""Phase 4B-1 deterministic tests: source policy, canonical signed manifests and
prepared release directories. Real ssh-keygen Ed25519 keys (throwaway, test
only); temporary paths replace the fixed root locations inside the test only."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'webdesktop-deploy/scripts/release_trust.py'


def load():
    spec = importlib.util.spec_from_file_location('release_trust_under_test', SCRIPT)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


m = load()
SHA_A = 'a'*40
SHA_B = 'b'*40


def keygen(directory, name):
    path = Path(directory)/name
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'test-only', '-f', str(path)], check=True)
    os.chmod(path.with_suffix('.pub'), 0o644)
    return path


def artifact(path, commit, branch, files=None, tweak=None):
    """Synthetic runtime package with the real staging_bundle manifest shape."""
    files = files or {'app/server.mjs': (b'export {}\n', 0o644), 'app/node': (b'\x7fELF-test', 0o755),
                      'app/lib/a.js': (b'module.exports=1\n', 0o644), 'source.bundle': (b'# v2 git bundle (test)\n', 0o644)}
    manifest = dict(schema=1, commit=commit, branch=branch, os='debian12', arch='amd64', node=m.bundle.NODE,
                    node_modules_abi='127', glibc='2.36', files={})
    for name, (data, mode) in sorted(files.items()):
        manifest['files'][name] = dict(type='file', size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=mode)
    manifest['files']['app/lib/link.js'] = dict(type='symlink', target='a.js')
    if tweak: tweak(manifest, files)
    raw = json.dumps(manifest, sort_keys=True).encode()
    with tarfile.open(path, 'w:gz') as tar:
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw); tar.addfile(info, io.BytesIO(raw))
        for name, (data, mode) in sorted(files.items()):
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = mode; tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo('app/lib/link.js'); info.type = tarfile.SYMTYPE; info.linkname = 'a.js'; tar.addfile(info)
    os.chmod(path, 0o600)
    return Path(path)


class Env(unittest.TestCase):
    """Fresh temporary root layout for each test, owned by the test user."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='wd-4b1-')); self.addCleanup(shutil.rmtree, self.tmp, True)
        os.chmod(self.tmp, 0o755)
        etc = self.tmp/'etc'; etc.mkdir(mode=0o755)
        self.deploy = self.tmp/'opt'; self.deploy.mkdir(mode=0o750); os.chmod(self.deploy, 0o750)
        self.inputs = self.tmp/'inputs'; self.inputs.mkdir(mode=0o700)
        self.isolated = etc/'webdesktop-isolated'
        patches = dict(ROOT_UID=os.getuid(), POLICY_DIR=etc/'webdesktop-release', DEPLOY=self.deploy, RELEASES=self.deploy/'releases',
                       APP=self.deploy/'app', STATE_DIR=self.tmp/'var-lib-release', LOCK=self.tmp/'release.lock',
                       DEPLOYMENT_STATES=(etc/'webdesktop-staging', self.isolated))
        for name, value in patches.items():
            p = mock.patch.object(m, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(m, 'service_gid', lambda: os.getgid()); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(m.bundle, 'ROOT_UID', os.getuid()); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(m, 'TRUSTED_ANCESTORS', frozenset(self.tmp.parents)); p.start(); self.addCleanup(p.stop)
        self.keys = self.tmp/'keys'; self.keys.mkdir(mode=0o700)
        self.ci = keygen(self.keys, 'ci'); self.other = keygen(self.keys, 'other')

    def sources(self, *argv):
        args = types.SimpleNamespace(op=argv[0], channel=None, signer_principal=None, signer_key_file=None)
        for flag, value in zip(argv[1::2], argv[2::2]): setattr(args, flag.lstrip('-').replace('-', '_'), value)
        with mock.patch('sys.stdout', io.StringIO()): m.sources_command(args)

    def enable_cc(self, principal='ci-test-signer', key=None):
        self.sources('init')
        self.sources('add-source', '--channel', 'testing-cc', '--signer-principal', principal, '--signer-key-file', str((key or self.ci).with_suffix('.pub')))

    def sign(self, art, commit, channel='testing-cc', principal='ci-test-signer', key=None, name='release.json'):
        out = self.inputs/name
        for p in (out, Path(str(out)+'.sig')):
            if p.exists(): p.unlink()
        with mock.patch('sys.stdout', io.StringIO()):
            m.sign_command(types.SimpleNamespace(artifact=art, commit=commit, channel=channel, principal=principal, key=key or self.ci, output=out, name='staging-runtime.tar.gz'))
        return out

    def resign(self, release_path, release, key=None):
        data = m.canonical(release)
        release_path.write_bytes(data); Path(str(release_path)+'.sig').unlink()
        subprocess.run(['ssh-keygen', '-q', '-Y', 'sign', '-f', str(key or self.ci), '-n', m.NAMESPACE, str(release_path)], check=True)
        os.chmod(str(release_path)+'.sig', 0o600)

    def read(self, path):
        return Path(path).read_bytes(), Path(str(path)+'.sig').read_bytes()

    def install_state(self, commit, branch=None, source=None, release_path=None):
        self.isolated.mkdir(mode=0o700)
        state = dict(schema=1, deployment_kind='isolated', authorized_new_vps=True, status='ready', phase='ready', commit=commit,
                     branch=branch or m.LEGACY_BRANCH, artifact_sha256='0'*64)
        if source:
            state['release_source'] = source
            release = json.loads(Path(release_path).read_bytes())
            state['artifact_sha256'] = release['artifact']['sha256']
            for name in ('release.json', 'release.json.sig'):
                shutil.copyfile(Path(release_path).with_name(Path(release_path).name+('.sig' if name.endswith('.sig') else '')), self.isolated/name)
                os.chmod(self.isolated/name, 0o600)
        (self.isolated/'state.json').write_text(json.dumps(state)); os.chmod(self.isolated/'state.json', 0o600)
        return state


class CanonicalEncodingTests(unittest.TestCase):
    def test_one_encoding_only(self):
        obj = dict(b=1, a=[True, None, 'x'])
        self.assertEqual(m.strict_json(m.canonical(obj), 1000), obj)
        for bad in (b'{"b":1,"a":[true,null,"x"]}', b'{"a": [true,null,"x"],"b":1}', b'{"a":[true,null,"x"],"b":1}\n',
                    b'{"a":1,"a":2}', b'{"a":1.0}', b'{"a":NaN}', '{"a":"é"}'.encode(), b'', b'{"a":' + b'1'*20000 + b'}'):
            with self.assertRaises(ValueError, msg=bad[:40]): m.strict_json(bad, 16000)

    def test_signer_key_parsing(self):
        with tempfile.TemporaryDirectory() as d:
            key = keygen(d, 'k'); text = key.with_suffix('.pub').read_text()
            normalized, fingerprint = m.parse_public_key(text)
            self.assertEqual(normalized, ' '.join(text.split()[:2]))
            ref = subprocess.run(['ssh-keygen', '-lf', str(key.with_suffix('.pub'))], capture_output=True, text=True, check=True).stdout.split()[1]
            self.assertEqual(fingerprint, ref)
            kind, b64 = text.split()[:2]
            import base64
            short = base64.b64encode(base64.b64decode(b64)[:-1]).decode()
            for bad in ('ssh-rsa '+b64, kind+' '+short, kind+' '+b64+'=', kind+' '+b64+'\n'+kind+' '+b64, kind):
                with self.assertRaises(ValueError, msg=bad[:30]): m.parse_public_key(bad)

    def test_registry_and_constants(self):
        spec = importlib.util.spec_from_file_location('si', ROOT/'webdesktop-deploy/scripts/staging_install.py')
        self.assertEqual(m.LEGACY_BRANCH, m.bundle.BRANCH)
        self.assertIn("BRANCH='feature/private-deploy-isolation'", (ROOT/'webdesktop-deploy/scripts/staging_install.py').read_text())
        self.assertEqual(m.CHANNELS['testing-cc'], dict(branch='feature/CC', independent_digest=False))
        self.assertEqual(m.REPOSITORY, 'github.com/isAliceYoung/fa')


class PolicyTests(Env):
    def test_absent_policy_is_exactly_the_legacy_rule(self):
        policy = m.load_policy()
        self.assertFalse(policy['explicit'])
        self.assertEqual(m.legacy_source(policy)['branch'], 'feature/private-deploy-isolation')
        self.assertIsNone(m.find_source(policy, 'testing-cc'))
        _, source = m.resolve_install_source(types.SimpleNamespace(release_manifest=None))
        self.assertEqual(source['trust'], 'independent-digest')

    def test_init_and_add_are_audited_and_private(self):
        self.enable_cc()
        d = m.POLICY_DIR
        self.assertEqual(oct(d.stat().st_mode & 0o777), '0o700')
        for name in ('sources.json', 'audit.jsonl'): self.assertEqual(oct((d/name).stat().st_mode & 0o777), '0o600')
        policy = m.load_policy()
        self.assertEqual(policy['revision'], 2)
        lines = (d/'audit.jsonl').read_bytes().splitlines()
        self.assertEqual([json.loads(l)['action'] for l in lines], ['init', 'add-source'])
        self.assertEqual(json.loads(lines[1])['prev'], hashlib.sha256(lines[0]).hexdigest())
        self.assertEqual(m.find_source(policy, 'testing-cc')['signers'][0]['principal'], 'ci-test-signer')
        with self.assertRaises(ValueError): self.sources('init')

    def test_policy_tampering_and_drift_refuse(self):
        self.enable_cc()
        f = m.POLICY_DIR/'sources.json'; a = m.POLICY_DIR/'audit.jsonl'
        good_f, good_a = f.read_bytes(), a.read_bytes()
        policy = json.loads(good_f)
        def edited(change):
            p = json.loads(good_f); change(p); return m.canonical(p)
        cases = [
            ('unregistered channel', edited(lambda p: p['sources'][1].update(channel='production')), good_a),
            ('branch not in registry', edited(lambda p: p['sources'][1].update(branch='main')), good_a),
            ('unsigned new source', edited(lambda p: p['sources'][1].update(independent_digest=True)), good_a),
            ('unregistered repository', edited(lambda p: p.update(repository='github.com/other/fa')), good_a),
            ('extra field', edited(lambda p: p.update(url='https://example.invalid')), good_a),
            ('fingerprint mismatch', edited(lambda p: p['sources'][1]['signers'][0].update(fingerprint='SHA256:'+'A'*43)), good_a),
            ('non-canonical', json.dumps(policy, indent=1).encode(), good_a),
            ('edit without audit', edited(lambda p: p['sources'][1]['signers'].clear()), good_a),
            ('audit rewritten', good_f, good_a.replace(b'"init"', b'"INIT"')),
            ('audit truncated', good_f, good_a.splitlines(True)[0]),
        ]
        for name, data, audit in cases:
            f.write_bytes(data); a.write_bytes(audit)
            with self.assertRaises(ValueError, msg=name): m.load_policy()
        f.write_bytes(good_f); a.write_bytes(good_a); m.load_policy()
        for mode, path in ((0o644, f), (0o640, a), (0o755, m.POLICY_DIR)):
            old = path.stat().st_mode & 0o777; os.chmod(path, mode)
            with self.assertRaises(ValueError, msg=str(path)+oct(mode)): m.load_policy()
            os.chmod(path, old)
        (m.POLICY_DIR/'extra').write_text('x')
        with self.assertRaises(ValueError): m.load_policy()
        (m.POLICY_DIR/'extra').unlink()
        f.rename(f.with_name('real')); f.symlink_to('real')
        with self.assertRaises(ValueError): m.load_policy()

    def test_only_root_administers(self):
        with mock.patch.object(m, 'ROOT_UID', os.getuid()+1):
            with self.assertRaises(ValueError): self.sources('init')
        self.assertFalse(os.path.lexists(m.POLICY_DIR))

    def test_in_use_sources_and_signers_cannot_be_removed(self):
        self.enable_cc()
        self.install_state(SHA_A)
        with self.assertRaises(ValueError): self.sources('remove-source', '--channel', 'development-legacy')
        self.sources('add-signer', '--channel', 'testing-cc', '--signer-principal', 'second-signer', '--signer-key-file', str(self.other.with_suffix('.pub')))
        self.sources('remove-signer', '--channel', 'testing-cc', '--signer-principal', 'second-signer')
        self.assertEqual(m.load_policy()['revision'], 4)
        for bad in (('add-signer', '--channel', 'testing-cc', '--signer-principal', 'ci-test-signer', '--signer-key-file', str(self.other.with_suffix('.pub'))),
                    ('add-signer', '--channel', 'testing-cc', '--signer-principal', 'Bad Principal', '--signer-key-file', str(self.other.with_suffix('.pub'))),
                    ('add-signer', '--channel', 'testing-cc', '--signer-principal', 'dup-key', '--signer-key-file', str(self.ci.with_suffix('.pub')))):
            with self.assertRaises(ValueError, msg=bad[3]): self.sources(*bad)
        os.chmod(self.other.with_suffix('.pub'), 0o666)
        with self.assertRaises(ValueError): self.sources('add-signer', '--channel', 'testing-cc', '--signer-principal', 'writable-key', '--signer-key-file', str(self.other.with_suffix('.pub')))


class SignedBase(Env):
    def setUp(self):
        super().setUp()
        self.enable_cc()
        self.art = artifact(self.inputs/'runtime.tar.gz', SHA_A, 'feature/CC')
        self.rel = self.sign(self.art, SHA_A)
        self.digest = m.bundle.digest(self.art)

    def refuse(self, data, signature, msg=''):
        with self.assertRaises(ValueError, msg=msg): m.authorize(m.load_policy(), data, signature)


class SignedReleaseTests(SignedBase):

    def test_valid_release_proves_source_and_artifact(self):
        release, source = m.authorize(m.load_policy(), *self.read(self.rel))
        self.assertEqual((source['channel'], source['branch'], source['trust'], source['signer']), ('testing-cc', 'feature/CC', 'signed-manifest', 'ci-test-signer'))
        m.check_artifact(release, self.art, self.digest, SHA_A)
        manifest = m.bundle.extract(self.art, self.digest, SHA_A, self.tmp/'out', branch='feature/CC', inner_sha256=release['inner_manifest_sha256'])
        self.assertEqual(manifest['branch'], 'feature/CC')
        args = types.SimpleNamespace(release_manifest=self.rel, artifact=self.art, artifact_sha256=self.digest, commit=SHA_A)
        resolved, source2 = m.resolve_install_source(args)
        self.assertEqual(source2, source); self.assertEqual(resolved['_bytes'], self.rel.read_bytes())

    def test_signed_fields_cannot_be_changed(self):
        data, sig = self.read(self.rel); release = json.loads(data)
        for name, change in [('repository', lambda r: r.update(repository='github.com/other/fa')), ('branch', lambda r: r.update(branch='main')),
                             ('channel', lambda r: r.update(channel='development-legacy', branch=m.LEGACY_BRANCH)),
                             ('commit', lambda r: r.update(commit=SHA_B)), ('size', lambda r: r['artifact'].update(size=r['artifact']['size']+1)),
                             ('digest', lambda r: r['artifact'].update(sha256='0'*64)), ('inner', lambda r: r.update(inner_manifest_sha256='0'*64)),
                             ('signer', lambda r: r.update(signer='other-signer')), ('installer', lambda r: r['installer'].update(max=9))]:
            changed = dict(release, artifact=dict(release['artifact']), installer=dict(release['installer'])); change(changed)
            self.refuse(m.canonical(changed), sig, name)

    def test_unregistered_or_malformed_values_refuse_even_when_validly_signed(self):
        data, _ = self.read(self.rel); release = json.loads(data)
        for name, change in [('unregistered repository', lambda r: r.update(repository='github.com/evil/fa')),
                             ('unregistered branch', lambda r: r.update(branch='feature/evil')),
                             ('unregistered channel', lambda r: r.update(channel='production')),
                             ('truncated sha', lambda r: r.update(commit='a'*39)), ('uppercase sha', lambda r: r.update(commit='A'*40)),
                             ('arch', lambda r: r.update(platform=dict(r['platform'], arch='arm64'))),
                             ('abi', lambda r: r.update(platform=dict(r['platform'], node_modules_abi='115'))),
                             ('glibc', lambda r: r.update(platform=dict(r['platform'], glibc='2.31'))),
                             ('node', lambda r: r.update(platform=dict(r['platform'], node='20.0.0'))),
                             ('installer too new', lambda r: r.update(installer=dict(min=2, max=3))),
                             ('unknown field', lambda r: r.update(url='https://example.invalid/x')),
                             ('boolean size', lambda r: r.update(artifact=dict(r['artifact'], size=True)))]:
            changed = json.loads(data); change(changed)
            self.resign(self.rel, changed)
            self.refuse(*self.read(self.rel), msg=name)

    def test_signature_identity_and_namespace(self):
        data, sig = self.read(self.rel)
        flipped = bytearray(sig); flipped[60] = ord('A') if flipped[60] != ord('A') else ord('B')
        self.refuse(data, bytes(flipped), 'corrupted signature')
        self.refuse(data, b'', 'empty signature'); self.refuse(data, sig+b'x'*9000, 'oversized')
        # Same principal name, unregistered key.
        self.resign(self.rel, json.loads(data), key=self.other); self.refuse(*self.read(self.rel), msg='wrong key')
        # Registered key, but for another source only.
        self.sources('add-signer', '--channel', 'development-legacy', '--signer-principal', 'legacy-signer', '--signer-key-file', str(self.other.with_suffix('.pub')))
        self.resign(self.rel, dict(json.loads(data), signer='legacy-signer'), key=self.other); self.refuse(*self.read(self.rel), msg='signer of another source')
        # Valid key and principal, wrong namespace.
        self.rel.write_bytes(data); Path(str(self.rel)+'.sig').unlink()
        subprocess.run(['ssh-keygen', '-q', '-Y', 'sign', '-f', str(self.ci), '-n', 'file', str(self.rel)], check=True)
        self.refuse(*self.read(self.rel), msg='namespace')

    def test_reordered_manifest_with_matching_signature_refused(self):
        data, _ = self.read(self.rel)
        reordered = json.dumps(json.loads(data), sort_keys=False, separators=(',', ':'), indent=None)[::-1][::-1]
        pretty = json.dumps(json.loads(data), indent=1).encode()
        for variant in (pretty, (reordered.replace('"schema":1,', '') .replace('{', '{"schema":1,', 1)).encode()):
            if variant == data: continue
            self.rel.write_bytes(variant); Path(str(self.rel)+'.sig').unlink()
            subprocess.run(['ssh-keygen', '-q', '-Y', 'sign', '-f', str(self.ci), '-n', m.NAMESPACE, str(self.rel)], check=True)
            self.refuse(*self.read(self.rel), msg='non-canonical but signed')

    def test_artifact_chain(self):
        release, _ = m.authorize(m.load_policy(), *self.read(self.rel))
        with self.assertRaises(ValueError): m.check_artifact(release, self.art, '0'*64, SHA_A)
        with self.assertRaises(ValueError): m.check_artifact(release, self.art, self.digest, SHA_B)
        # Content AND inner manifest changed consistently: the signed outer digest no longer matches.
        def tamper(manifest, files):
            files['app/server.mjs'] = (b'malicious\n', 0o644)
            manifest['files']['app/server.mjs'].update(size=10, sha256=hashlib.sha256(b'malicious\n').hexdigest())
        evil = artifact(self.inputs/'evil.tar.gz', SHA_A, 'feature/CC', tweak=tamper)
        with self.assertRaises(ValueError): m.check_artifact(release, evil, m.bundle.digest(evil), SHA_A)
        # A signed release naming the evil digest but the ORIGINAL inner manifest: extraction refuses.
        r = dict(release, artifact=dict(release['artifact'], size=evil.stat().st_size, sha256=m.bundle.digest(evil)))
        self.resign(self.rel, r)
        release2, _ = m.authorize(m.load_policy(), *self.read(self.rel))
        m.check_artifact(release2, evil, m.bundle.digest(evil), SHA_A)
        with self.assertRaises(ValueError):
            m.bundle.extract(evil, m.bundle.digest(evil), SHA_A, self.tmp/'evil-out', branch='feature/CC', inner_sha256=release2['inner_manifest_sha256'])
        self.assertFalse((self.tmp/'evil-out').exists())

    def test_branch_cannot_be_substituted(self):
        with self.assertRaises(ValueError):  # CC artifact through the legacy (unsigned) rule
            m.bundle.extract(self.art, self.digest, SHA_A, self.tmp/'x')
        legacy = artifact(self.inputs/'legacy.tar.gz', SHA_B, m.LEGACY_BRANCH)
        with self.assertRaises(ValueError):  # legacy artifact relabelled as feature/CC
            m.bundle.extract(legacy, m.bundle.digest(legacy), SHA_B, self.tmp/'y', branch='feature/CC')
        with self.assertRaises(ValueError):  # signing tool refuses a mismatched label too
            self.sign(legacy, SHA_B, channel='testing-cc', name='bad.json')
        m.bundle.extract(legacy, m.bundle.digest(legacy), SHA_B, self.tmp/'z')

    def test_installed_state_reproof(self):
        _, source = m.authorize(m.load_policy(), *self.read(self.rel))
        state = self.install_state(SHA_A, 'feature/CC', source, self.rel)
        m.authorized_state(state, self.isolated)
        (self.isolated/'release.json').write_bytes((self.isolated/'release.json').read_bytes().replace(b'"created":"', b'"created":"1'))
        with self.assertRaises(ValueError): m.authorized_state(state, self.isolated)
        shutil.copyfile(self.rel, self.isolated/'release.json')
        m.authorized_state(state, self.isolated)
        for change in (dict(branch=m.LEGACY_BRANCH), dict(commit=SHA_B), dict(release_source=dict(source, signer='x'))):
            with self.assertRaises(ValueError, msg=str(change)): m.authorized_state(dict(state, **change), self.isolated)
        with self.assertRaises(ValueError): self.sources('remove-signer', '--channel', 'testing-cc', '--signer-principal', 'ci-test-signer')
        with self.assertRaises(ValueError): self.sources('remove-source', '--channel', 'testing-cc')
        m.authorized_state(state, self.isolated)

    def test_legacy_state_and_disabled_legacy(self):
        m.authorized_state(dict(branch=m.LEGACY_BRANCH, commit=SHA_A), self.isolated)
        with self.assertRaises(ValueError): m.authorized_state(dict(branch='feature/CC', commit=SHA_A), self.isolated)
        self.sources('remove-source', '--channel', 'development-legacy')
        with self.assertRaises(ValueError): m.authorized_state(dict(branch=m.LEGACY_BRANCH, commit=SHA_A), self.isolated)
        with self.assertRaises(ValueError): m.resolve_install_source(types.SimpleNamespace(release_manifest=None))

    def test_release_input_protection(self):
        args = types.SimpleNamespace(release_manifest=self.rel, artifact=self.art, artifact_sha256=self.digest, commit=SHA_A)
        os.chmod(self.rel, 0o644)
        with self.assertRaises(ValueError): m.resolve_install_source(args)
        os.chmod(self.rel, 0o600); os.chmod(self.inputs, 0o777)
        with self.assertRaises(ValueError): m.resolve_install_source(args)
        os.chmod(self.inputs, 0o700)
        with self.assertRaises(ValueError): m.resolve_install_source(types.SimpleNamespace(**dict(vars(args), release_manifest=Path('relative.json'))))
        m.resolve_install_source(args)

    def test_optimized_interpreters_still_refuse(self):
        data, sig = self.read(self.rel)
        bad = data.replace(b'"commit":"'+SHA_A.encode(), b'"commit":"'+SHA_B.encode())
        (self.inputs/'bad.json').write_bytes(bad)
        code = '''import importlib.util,sys,json
from pathlib import Path
from unittest import mock
s=importlib.util.spec_from_file_location('rt',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.ROOT_UID=int(sys.argv[2]);m.POLICY_DIR=Path(sys.argv[3])
policy=m.load_policy()
for data in (Path(sys.argv[4]).read_bytes(),Path(sys.argv[4]).read_bytes()+b' '):
    try:m.authorize(policy,data,Path(sys.argv[5]).read_bytes())
    except ValueError:continue
    print('ACCEPTED');sys.exit(3)
m.authorize(policy,Path(sys.argv[6]).read_bytes(),Path(sys.argv[5]).read_bytes())
print('ok')
'''
        for flag in ('-O', '-OO'):
            r = subprocess.run([sys.executable, '-I', flag, '-c', code, str(SCRIPT), str(os.getuid()), str(m.POLICY_DIR), str(self.inputs/'bad.json'), str(self.rel)+'.sig', str(self.rel)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'ok'), flag+r.stderr[-500:])


class PreparedReleaseTests(SignedBase):
    def setUp(self):
        super().setUp()
        self.install_state(SHA_B)  # installed legacy deployment (unchanged by preparation)
        self.app = self.deploy/'app'; self.app.mkdir(mode=0o750); (self.app/'server.mjs').write_text('installed'); os.chmod(self.app, 0o750)

    def prepare(self, rel=None, art=None, digest=None):
        args = types.SimpleNamespace(release_manifest=rel or self.rel, artifact=art or self.art, artifact_sha256=digest or self.digest)
        with mock.patch('sys.stdout', io.StringIO()) as out:
            m.prepare_command(args)
        return json.loads(out.getvalue())

    def status(self):
        with mock.patch('sys.stdout', io.StringIO()) as out: m.status_command(None)
        return json.loads(out.getvalue())

    def test_prepare_is_immutable_verified_and_does_not_switch(self):
        before = (self.app/'server.mjs').read_bytes(), sorted(p.name for p in self.deploy.iterdir())
        result = self.prepare()
        self.assertEqual(result['prepared']['commit'], SHA_A); self.assertFalse(result['switched'])
        d = m.RELEASES/SHA_A
        self.assertEqual(sorted(p.name for p in d.iterdir()), ['app', 'manifest.json', 'release.json', 'release.json.sig'])
        self.assertEqual(oct((d/'app/server.mjs').stat().st_mode & 0o777), '0o640')
        self.assertEqual(oct((d/'app/node').stat().st_mode & 0o777), '0o750')
        self.assertEqual(oct((d/'app/lib').stat().st_mode & 0o777), '0o750')
        self.assertEqual(os.readlink(d/'app/lib/link.js'), 'a.js')
        self.assertFalse((d/'app/source.bundle').exists())
        self.assertEqual(oct((m.STATE_DIR/'bundles'/(SHA_A+'.bundle')).stat().st_mode & 0o777), '0o600')
        self.assertEqual((self.app/'server.mjs').read_bytes(), before[0])
        self.assertEqual(sorted(p.name for p in self.deploy.iterdir()), sorted(before[1]+['releases']))
        self.assertEqual(m.verify_prepared(SHA_A)['verified'], True)
        with self.assertRaises(ValueError): self.prepare()  # never overwritten
        status = self.status()
        self.assertEqual([r['commit'] for r in status['releases'] if r['verified']], [SHA_A])
        self.assertEqual((status['app_layout'], status['switching_supported']), ('directory', False))
        text = json.dumps(status)
        for secret in ('PRIVATE KEY', self.ci.read_text().split('\n')[1]): self.assertNotIn(secret, text)
        audit = [json.loads(l)['action'] for l in (m.STATE_DIR/'audit.jsonl').read_bytes().splitlines()]
        self.assertEqual(audit, ['prepare-start', 'prepare-done'])

    def test_two_versions_coexist_independently(self):
        self.prepare()
        art2 = artifact(self.inputs/'runtime2.tar.gz', 'c'*40, 'feature/CC', files={'app/server.mjs': (b'v2\n', 0o644), 'source.bundle': (b'b2', 0o644), 'app/lib/a.js': (b'2', 0o644)})
        rel2 = self.sign(art2, 'c'*40, name='release2.json')
        self.prepare(rel2, art2, m.bundle.digest(art2))
        self.assertEqual({r['commit'] for r in self.status()['releases'] if r['verified']}, {SHA_A, 'c'*40})
        self.assertEqual((m.RELEASES/SHA_A/'app/server.mjs').read_bytes(), b'export {}\n')

    def test_tampered_prepared_release_is_not_recognized(self):
        self.prepare(); d = m.RELEASES/SHA_A
        def check(label, apply, undo):
            apply()
            try:
                with self.assertRaises(ValueError, msg=label): m.verify_prepared(SHA_A)
            finally: undo()
            m.verify_prepared(SHA_A)
        extra = d/'app/unknown.js'
        check('unknown file', lambda: (extra.write_text('x'), os.chmod(extra, 0o640)), extra.unlink)
        check('unknown dir', lambda: (d/'app/x').mkdir(mode=0o750), lambda: (d/'app/x').rmdir())
        check('mode drift', lambda: os.chmod(d/'app/server.mjs', 0o644), lambda: os.chmod(d/'app/server.mjs', 0o640))
        check('dir mode drift', lambda: os.chmod(d/'app/lib', 0o755), lambda: os.chmod(d/'app/lib', 0o750))
        link = self.tmp/'hard'
        check('hard link', lambda: os.link(d/'app/server.mjs', link), link.unlink)
        data = (d/'app/lib/a.js').read_bytes()
        check('content', lambda: (d/'app/lib/a.js').write_bytes(b'evil'), lambda: (d/'app/lib/a.js').write_bytes(data))
        check('symlink retarget', lambda: ((d/'app/lib/link.js').unlink(), (d/'app/lib/link.js').symlink_to('../server.mjs')),
              lambda: ((d/'app/lib/link.js').unlink(), (d/'app/lib/link.js').symlink_to('a.js')))
        saved = self.tmp/'saved-lib'
        check('directory replaced by symlink', lambda: ((d/'app/lib').rename(saved), (d/'app/lib').symlink_to(str(saved))),
              lambda: ((d/'app/lib').unlink(), saved.rename(d/'app/lib')))
        meta = (d/'release.json').read_bytes()
        check('release metadata', lambda: (d/'release.json').write_bytes(meta.replace(b'"created":"', b'"created":"1')), lambda: (d/'release.json').write_bytes(meta))
        check('metadata extra', lambda: ((d/'notes').write_text('x'), os.chmod(d/'notes', 0o640)), (d/'notes').unlink)

    def test_unknown_or_interrupted_entries_block_preparation(self):
        m.RELEASES.mkdir(mode=0o750); os.chmod(m.RELEASES, 0o750)
        (m.RELEASES/'not-a-release').mkdir()
        with self.assertRaises(ValueError): self.prepare()
        (m.RELEASES/'not-a-release').rmdir()
        outside = self.tmp/'outside'; outside.mkdir()
        (m.RELEASES/SHA_A).symlink_to(outside)
        with self.assertRaises(ValueError): self.prepare()  # existing name is never replaced
        (m.RELEASES/SHA_A).unlink()
        (m.RELEASES/('d'*40)).mkdir(mode=0o750)
        with self.assertRaises(ValueError): self.prepare()  # unrecorded candidate-looking entry
        (m.RELEASES/('d'*40)).rmdir()
        # Interrupted extraction: record says staging, nothing recognized.
        with mock.patch.object(m.bundle, 'extract', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError): self.prepare()
        st = self.status()
        self.assertEqual(len(st['incomplete']), 1); self.assertEqual(st['releases'][0]['state'], 'staging'); self.assertFalse(st['releases'][0]['verified'])
        with self.assertRaises(ValueError): m.verify_prepared(SHA_A)
        with self.assertRaises(ValueError): self.prepare()  # STALE
        with mock.patch('sys.stdout', io.StringIO()): m.discard_incomplete(types.SimpleNamespace(commit=SHA_A))
        self.assertEqual(self.status()['incomplete'], [])
        self.prepare(); m.verify_prepared(SHA_A)
        with self.assertRaises(ValueError): m.discard_incomplete(types.SimpleNamespace(commit=SHA_A))  # prepared releases are not discardable

    def test_disk_space_concurrency_and_releases_link(self):
        usage = shutil.disk_usage(self.tmp)
        with mock.patch.object(m.shutil, 'disk_usage', return_value=usage._replace(free=m.RESERVE)):
            with self.assertRaises(ValueError) as e: self.prepare()
        self.assertIn('DISK_SPACE', str(e.exception)); self.assertEqual(self.status()['incomplete'], [])
        with m.exclusive():
            with self.assertRaises(ValueError) as e: self.prepare()
        self.assertIn('BUSY', str(e.exception))
        m.RELEASES.rmdir(); target = self.tmp/'elsewhere'; target.mkdir(mode=0o750); m.RELEASES.symlink_to(target)
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(list(target.iterdir()), [])

    def test_app_layout_recognition(self):
        self.prepare()
        self.assertEqual(m.app_layout(), 'directory')
        self.app.rename(self.deploy/'app.saved'); self.app.symlink_to('releases/'+SHA_A+'/app')
        self.assertEqual(m.app_layout(), 'release-link')
        self.app.unlink(); self.app.symlink_to('/tmp')
        self.assertEqual(m.app_layout(), 'unknown')
        self.app.unlink(); (self.deploy/'app.saved').rename(self.app)

    def test_preparation_requires_an_authorized_installed_deployment(self):
        (self.isolated/'state.json').write_text(json.dumps(dict(status='installing', commit=SHA_B, branch=m.LEGACY_BRANCH)))
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(os.path.lexists(m.RELEASES))


class NoAssertTests(unittest.TestCase):
    def test_release_trust_has_no_optimizable_asserts(self):
        import ast
        self.assertFalse(any(isinstance(n, ast.Assert) for n in ast.walk(ast.parse(SCRIPT.read_text()))))


if __name__ == '__main__':
    unittest.main()
