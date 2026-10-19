from pathlib import Path
import subprocess
import unittest

class NetworkPolicyTests(unittest.TestCase):
    def test_bind_validation_and_actual_default_listener(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', str(root / 'tests/network_policy_checks.mjs')],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
