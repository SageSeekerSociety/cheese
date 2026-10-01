"""ensure-apt.sh against a stub apt: a stalled mirror must fail or recover within minutes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("ensure-apt.sh")

STUBS = {
    # Every package is missing, so the script always reaches apt.
    "dpkg": "#!/bin/sh\nexit 1\n",
    "sudo": '#!/bin/sh\nexec "$@"\n',
    "sleep": "#!/bin/sh\nexit 0\n",
    "flock": "#!/bin/sh\nexit 0\n",
    # Records each call; fails the first $FAILS calls with $MESSAGE.
    "apt-get": """#!/bin/sh
echo "$*" >> "$STATE/calls"
n=$(wc -l < "$STATE/calls")
if [ "$n" -le "$FAILS" ]; then echo "$MESSAGE"; exit 100; fi
echo ok
""",
}


class EnsureApt(unittest.TestCase):
    def run_script(self, fails, message):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            for name, body in STUBS.items():
                stub = root / "bin" / name
                stub.write_text(body)
                stub.chmod(0o755)
            env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                   "STATE": str(root), "FAILS": str(fails), "MESSAGE": message,
                   "CHEESE_APT_LOCK": str(root / "lock")}
            result = subprocess.run(["bash", str(SCRIPT), "zsh"], env=env,
                                    capture_output=True, text=True, timeout=60)
            calls = (root / "calls").read_text().splitlines() if (root / "calls").exists() else []
            return result, calls

    def test_a_failed_download_is_retried_until_it_succeeds(self):
        result, calls = self.run_script(2, "E: Failed to fetch http://mirror/InRelease  Connection timed out")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([" update" in call for call in calls], [True, True, True, False])
        self.assertTrue(calls[-1].endswith("install -y -qq zsh"), calls[-1])

    def test_a_mirror_that_stays_down_fails_the_step(self):
        result, calls = self.run_script(99, "E: Failed to fetch http://mirror/InRelease  Could not connect")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 4)
        self.assertIn("Could not connect", result.stderr)

    def test_other_errors_are_not_retried(self):
        result, calls = self.run_script(99, "E: Unable to locate package zsh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_every_apt_call_bounds_its_network_waits(self):
        _, calls = self.run_script(0, "")
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("Acquire::http::Timeout=30", call)
            self.assertIn("Acquire::https::Timeout=30", call)
            self.assertIn("APT::Update::Error-Mode=any", call)


if __name__ == "__main__":
    unittest.main()
