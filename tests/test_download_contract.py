"""Portable native download policy and partial name classification tests.

These are supplemental; never substitute for visible Chromium/real UID tests.
"""
import ast
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'guest-runtime/secure_fs.py').read_text())
function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='incomplete_download')
scope={};exec(compile(ast.Module(body=[function],type_ignores=[]),'<portable native-name classifier>','exec'),scope)
incomplete_download=scope['incomplete_download']

class DownloadContractTests(unittest.TestCase):
    def test_native_partial_names_include_nested_and_case_variants(self):
        for p in ('Downloads/a.crdownload','Downloads/A.CRDOWNLOAD','Downloads/.org.chromium.a','Downloads/x.crdownload/a.txt'):
            self.assertTrue(incomplete_download(p),p)
    def test_complete_names_and_unicode_are_not_incomplete(self):
        for p in ('Downloads/中文 🧪.txt','Downloads/a.crdownload.txt','Downloads/a (1).txt','Documents/x.part','Downloads/crdownload'):
            self.assertFalse(incomplete_download(p),p)
    def test_managed_policy_has_only_fixed_guest_destination(self):
        policy=json.loads((ROOT/'gui-runtime/download-policy.json').read_text())
        self.assertEqual(policy,{'DownloadDirectory':'/home/wdguest/Downloads','PromptForDownloadLocation':False})
    def test_runtime_package_does_not_gain_observer_or_tls_fixture(self):
        script=(ROOT/'webdesktop-deploy/scripts/setup-browser-layer.sh').read_text()
        self.assertNotIn('download_bridge_vm',script)
        self.assertNotIn('certutil',script)
        self.assertNotIn('--no-sandbox',script)
    def test_old_stable_installer_does_not_enable_download_experiment(self):
        stable=(ROOT/'webdesktop-deploy/install.sh').read_text()
        self.assertNotIn('download-policy.json',stable)

if __name__=='__main__':unittest.main()
