"""Exercise gateway rollout ordering and rollback without touching a daemon."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "release-gateway.sh"
FAKE = """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps({"args":args,"image":os.environ.get("GATEWAY_IMAGE"),"config":os.environ.get("GATEWAY_CONFIG")}) + "\\n")
if args[0] == "inspect": print("sha256:previous")
elif args[:2] == ["image", "inspect"]: print("a" * 40)
elif args[0] == "create": print("candidate")
elif args[0] == "cp": pathlib.Path(args[-1]).write_text("model_list: []\\n")
elif args[0] == "run" and os.environ.get("FAIL_PREFLIGHT"): sys.exit(1)
elif args[0] == "compose" and os.environ.get("FAIL_RELEASE") and os.environ["GATEWAY_IMAGE"] != "sha256:previous": sys.exit(1)
"""


class GatewayReleaseTest(unittest.TestCase):
    def run_release(self, **overrides):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "compose").mkdir()
            (root / "compose/.env").write_text("TEST=1\n")
            docker = root / "docker"
            docker.write_text(FAKE)
            docker.chmod(0o755)
            env = dict(
                os.environ,
                PATH=f"{root}:{os.environ['PATH']}",
                GATEWAY_HOME=folder,
                GITHUB_ACTIONS="true",
                GATEWAY_ALLOW_INTERRUPT="1",
                GITHUB_RUN_ID="123",
                GITHUB_RUN_ATTEMPT="1",
                CALLS=str(root / "calls"),
            )
            env.update(overrides)
            result = subprocess.run(
                ["bash", str(SCRIPT), "a" * 40], env=env, capture_output=True, text=True
            )
            calls = (
                [json.loads(line) for line in (root / "calls").read_text().splitlines()]
                if (root / "calls").exists()
                else []
            )
            return result, calls

    def test_requires_explicit_interruption_acknowledgement(self):
        result, calls = self.run_release(GATEWAY_ALLOW_INTERRUPT="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_failed_image_test_never_changes_live_service(self):
        result, calls = self.run_release(FAIL_PREFLIGHT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["args"][0] == "compose" for c in calls))

    def test_releases_only_gateway_after_saving_previous_config(self):
        result, calls = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [c["args"][0] for c in calls]
        self.assertLess(actions.index("cp"), actions.index("compose"))
        rollout = [c for c in calls if c["args"][0] == "compose"]
        self.assertEqual(len(rollout), 1)
        self.assertEqual(
            rollout[0]["args"][-7:],
            ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "150", "litellm"],
        )

    def test_unhealthy_release_restores_old_image_and_config(self):
        result, calls = self.run_release(FAIL_RELEASE="1")
        self.assertNotEqual(result.returncode, 0)
        rollout = [c for c in calls if c["args"][0] == "compose"]
        self.assertEqual(len(rollout), 2)
        self.assertEqual(rollout[-1]["image"], "sha256:previous")
        self.assertTrue(rollout[-1]["config"].endswith("/previous.yaml"))


if __name__ == "__main__":
    unittest.main()
