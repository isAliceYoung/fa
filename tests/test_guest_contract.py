"""Portable boundary/packaging checks. Real permission tests are linux_guest_layer.py."""
import ast
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GuestContractTests(unittest.TestCase):
    def test_every_host_operation_route_uses_only_authenticated_guest_gate(self):
        for folder in ("sys", "gui", "browser"):
            for path in (ROOT / "src/app/api" / folder).rglob("route.ts"):
                text = path.read_text(encoding="utf-8")
                imports = re.findall(r'from "([^"]+)"', text)
                self.assertEqual(imports, ["@/lib/guest-api"], str(path))
                for forbidden in ("readFile", "spawn(", "process.kill", "createReadStream", "@/lib/sys", "@/lib/gui"):
                    self.assertNotIn(forbidden, text)

    def test_startup_has_no_legacy_execution_websocket_import(self):
        text = (ROOT / "src/instrumentation.ts").read_text(encoding="utf-8")
        self.assertIn('import("./lib/guest-ws")', text)
        self.assertNotIn('import("./lib/browser-ws")', text)
        self.assertNotIn("startDeployWatcher", text)

    def test_guest_distribution_is_only_two_stdlib_runtime_files(self):
        paths = list((ROOT / "guest-runtime").glob("*.py"))
        self.assertEqual({p.name for p in paths}, {"agent.py", "secure_fs.py"})
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    self.assertNotIn(module.split(".")[0], {"subprocess", "auth", "db", "psycopg", "requests"})

    def test_fixed_ipc_has_no_environment_target_or_local_filesystem_fallback(self):
        text = (ROOT / "src/lib/guest-client.mjs").read_text(encoding="utf-8")
        self.assertIn("const SOCKET = '/run/webdesktop-guest/guest.sock'", text)
        self.assertNotIn("process.env", text)
        self.assertNotIn("fs.readFile", text)
        self.assertNotIn("Cookie:", text)

    def test_node_body_limits_and_unavailable_service_errors(self):
        result = subprocess.run(["node", str(ROOT / "tests/guest_client_checks.mjs")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
