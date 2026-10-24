"""Safety validation under optimizing interpreters; not Linux DAC substitutes."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_staging_bundle as archive_tests

ROOT=Path(__file__).resolve().parents[1]
m=archive_tests.m


class OptimizedArchiveTests(archive_tests.StagingArchiveTests):
    optimization='-O'
    def setUp(self):
        def extract(archive,digest,commit,destination):
            code='''import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('bundle',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
try:m.extract(Path(sys.argv[2]),sys.argv[3],sys.argv[4],Path(sys.argv[5]))
except (ValueError,KeyError) as e:print(type(e).__name__);sys.exit(1)
'''
            r=subprocess.run([sys.executable,'-I',self.optimization,'-c',code,str(ROOT/'webdesktop-deploy/scripts/staging_bundle.py'),str(archive),digest,commit,str(destination)],capture_output=True,text=True,timeout=15)
            if r.returncode==1 and r.stdout.strip() in ('ValueError','KeyError'):raise ValueError('Optimized runtime rejected archive')
            if r.returncode:raise RuntimeError(r.stderr)
        self.mock=patch.object(m,'extract',extract);self.mock.start();self.addCleanup(self.mock.stop)


class DoubleOptimizedArchiveTests(OptimizedArchiveTests):
    optimization='-OO'


class IsolatedPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('isolated',ROOT/'webdesktop-deploy/scripts/isolated_deploy.py')
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
    def test_only_two_fixed_profiles(self):
        for attack in ('../../root','legacy','/tmp/state','production'):
            with self.assertRaises(ValueError):self.module.shared.Layout(attack)
        self.assertEqual(self.module.L.state,Path('/etc/webdesktop-isolated'))
        self.assertEqual(self.module.shared.STAGING.unit,'webdesktop-staging.service')
    def test_stable_recovery_fingerprint_excludes_transient_phase_not_budget(self):
        keys=('schema','deployment_kind','authorized_new_vps','branch','commit','artifact_sha256','administrators','origins','resource_policy','resource_inputs','swap_target_mib')
        record={k:i for i,k in enumerate(keys)}
        self.assertEqual(self.module.fixed_state({**record,'status':'failed','phase':'start-service'}),record)
        self.assertNotEqual(self.module.fixed_state({**record,'resource_policy':'changed'}),record)
    def test_no_optimization_removable_safety_assertions(self):
        for name in ('staging_install.py','staging_bundle.py','staging_check.py','isolated_deploy.py','release_trust.py'):
            tree=ast.parse((ROOT/'webdesktop-deploy/scripts'/name).read_text('utf-8'))
            self.assertFalse(any(isinstance(n,ast.Assert) for n in ast.walk(tree)),name)


if __name__=='__main__':unittest.main()
