"""Design guard only; real sshd connections belong to later Linux acceptance."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TunnelDesignTests(unittest.TestCase):
    def test_template_limits_destination_and_disables_session_and_other_channels(self):
        text = (ROOT / "webdesktop-deploy/templates/sshd-wdtunnel.conf.example").read_text()
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(lines[0], "Match User wdtunnel")
        self.assertNotIn("Match all", lines)
        self.assertIn("END of the top-level", text)
        settings = dict(line.split(None, 1) for line in lines[1:])
        for name, expected in {
            "AllowTcpForwarding": "local", "PermitOpen": "127.0.0.1:3000", "PermitListen": "none",
            "AllowStreamLocalForwarding": "no", "AllowAgentForwarding": "no", "X11Forwarding": "no",
            "PermitTTY": "no", "PermitTunnel": "no", "MaxSessions": "0", "ForceCommand": "/usr/bin/false",
            "AuthenticationMethods": "publickey", "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no",
        }.items():
            self.assertEqual(settings[name], expected)

    def test_bootstrap_does_not_install_tunnel_template_or_change_sshd(self):
        text = (ROOT / "webdesktop-deploy/bootstrap.sh").read_text()
        for forbidden in ("useradd", "sshd_config", "systemctl", "sshd-wdtunnel", "chown"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
