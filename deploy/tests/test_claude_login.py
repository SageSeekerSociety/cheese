"""Test logging the platform in to Claude and out, as the metering proxy sees it."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/metering-proxy/claude-login.sh"
FAKE_CLAUDE = """#!/bin/sh
[ "$1 $2" = "auth login" ] || exit 9
[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || exit 8
oauth='"accessToken":"at","refreshToken":"rt"'
oauth="$oauth"',"expiresAt":1900000000000,"refreshTokenExpiresAt":1902000000000'
printf '{"claudeAiOauth":{%s}}' "$oauth" > "$CLAUDE_CONFIG_DIR/.credentials.json"
"""


class ClaudeLoginTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.home = Path(self.folder.name)
        self.credential = self.home / "claude-credential/credential"
        claude = self.home / "claude"
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(0o755)
        self.env = dict(
            os.environ,
            METERING_PROXY_HOME=str(self.home),
            CLAUDE_BIN=str(claude),
            CLAUDE_CODE_OAUTH_TOKEN="inherited-token",
        )

    def tearDown(self):
        self.folder.cleanup()

    def run_script(self, *args, stdin=""):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
        )

    def test_a_setup_token_becomes_the_proxys_credential(self):
        result = self.run_script("setup-token", stdin="sk-ant-oat01-SETUP\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.credential.read_text().strip(), "sk-ant-oat01-SETUP")
        self.assertEqual(self.credential.stat().st_mode & 0o777, 0o600)
        self.assertIn("setup-token", self.run_script("status").stdout)

    def test_something_that_is_not_a_token_changes_nothing(self):
        result = self.run_script("setup-token", stdin="hello\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.credential.exists())

    def test_a_browser_login_leaves_its_pair_with_the_proxy_alone(self):
        result = self.run_script("login")

        self.assertEqual(result.returncode, 0, result.stderr)
        stored = json.loads(self.credential.read_text())["claudeAiOauth"]
        self.assertEqual(stored["refreshToken"], "rt")
        # The copy the login wrote is gone: the proxy is the pair's only holder.
        leftovers = [p.name for p in self.credential.parent.iterdir()]
        self.assertEqual(leftovers, ["credential"])
        status = self.run_script("status").stdout
        self.assertIn("log in again by", status)
        self.assertNotIn("rt", status.split())

    def test_logging_out_removes_the_credential(self):
        self.run_script("setup-token", stdin="sk-ant-oat01-SETUP\n")

        result = self.run_script("logout")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.credential.exists())
        self.assertIn("logged out", self.run_script("status").stdout)


if __name__ == "__main__":
    unittest.main()
