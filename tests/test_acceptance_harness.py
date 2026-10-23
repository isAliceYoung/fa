"""Static consistency of the disposable-VM acceptance harness (no VM required)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MonitorBounds(unittest.TestCase):
    def test_driver_monitor_durations_are_accepted_by_observer(self):
        # The C2 gate once passed 3600 s to an observer bounded at 1800 s: the
        # monitor exited at once and cleanup failed although every suite passed.
        driver = (ROOT / 'tests/download_bridge_driver.py').read_text()
        observer = (ROOT / 'tests/download_bridge_vm.py').read_text()
        low, high = map(int, re.search(r"if not (\d+)<=duration<=(\d+):raise ValueError\('Bounded monitor duration'\)", observer).groups())
        chosen = re.search(r"monitor=(\d+) if chosen=={'d1'} else (\d+)", driver)
        self.assertIsNotNone(chosen)
        for value in map(int, chosen.groups()):
            self.assertTrue(low <= value <= high, value)
        self.assertIn("RuntimeMaxSec='+str(monitor+20)", driver)


if __name__ == '__main__':
    unittest.main()
