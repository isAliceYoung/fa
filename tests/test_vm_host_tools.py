"""CI preparation must never silently become a normal server installer."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class VMHostToolGuards(unittest.TestCase):
    def run_guard(self,arguments,env):
        return subprocess.run([os.environ['BASH_FOR_TESTS'],'--noprofile','--norc',str(ROOT/'tests/prepare_vm_host.sh')]+arguments,env=env,capture_output=True,text=True,timeout=10)

    def test_unapproved_host_refused_before_package_operations(self):
        env=dict(os.environ);env.pop('GITHUB_ACTIONS',None);env['RUNNER_OS']='Linux'
        result=self.run_guard(['--fonts'],env)
        self.assertEqual(result.returncode,64,result.stderr)
        self.assertIn('no package operation performed',result.stderr)
        self.assertNotIn('CI host tools:',result.stdout)

    def test_unknown_option_cannot_inject_package_or_apt_arguments(self):
        env=dict(os.environ);env['GITHUB_ACTIONS']='true';env['RUNNER_OS']='Linux'
        result=self.run_guard(['--allow-unauthenticated'],env)
        self.assertEqual(result.returncode,64,result.stderr)
        self.assertIn('Unknown CI host-tool option',result.stderr)
        self.assertNotIn('CI host tools:',result.stdout)
