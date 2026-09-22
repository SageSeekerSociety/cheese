"""Run the gateway health workflow command against a fake container boundary."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy-drift.yml"


class GatewayHealthWorkflowTest(unittest.TestCase):
    def setUp(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        self.step = next(
            step
            for step in workflow["jobs"]["drift"]["steps"]
            if "gateway_supply_probe.py" in step.get("run", "")
        )

    def run_probe(self, status, missing_source=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake = root / "docker"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['CALLS']).write_text(json.dumps(sys.argv[1:]))\n"
                "pathlib.Path(os.environ['INPUT']).write_text(sys.stdin.read())\n"
                "print('probe result')\n"
                "sys.exit(int(os.environ['STATUS']))\n"
            )
            fake.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", self.step["run"]],
                cwd=root if missing_source else ROOT,
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "CALLS": str(root / "calls"),
                    "INPUT": str(root / "input"),
                    "STATUS": str(status),
                },
                capture_output=True,
                text=True,
            )
            return (
                result,
                json.loads((root / "calls").read_text())
                if (root / "calls").exists()
                else [],
                (root / "input").read_text() if (root / "input").exists() else "",
            )

    def test_checks_even_after_failed_deployment_step(self):
        # GitHub's explicit status function bypasses its implicit success() guard.
        self.assertEqual(self.step["if"], "${{ always() }}")
        self.assertLessEqual(self.step["timeout-minutes"], 1)
        self.assertFalse(self.step.get("continue-on-error", False))

    def test_default_probe_uses_container_credentials_and_never_generates(self):
        result, args, source = self.run_probe(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            args, ["exec", "-i", "cheese-gateway-litellm-1", "python", "-"]
        )
        self.assertEqual(
            source, (ROOT / "backend/scripts/gateway_supply_probe.py").read_text()
        )

    def test_probe_failure_keeps_workflow_red_without_restarting(self):
        result, args, _ = self.run_probe(1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Release gateway workflow", result.stdout)
        self.assertEqual(args[0], "exec")

    def test_missing_checkout_fails_before_container_execution(self):
        result, args, _ = self.run_probe(0, missing_source=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])

    def test_unavailable_docker_is_not_reported_healthy(self):
        result, _, _ = self.run_probe(125)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
