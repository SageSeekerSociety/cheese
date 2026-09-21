#!/usr/bin/env python3
"""Exercise release ordering against a real commit graph and a fake Docker host."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("auto_deploy", ROOT / "deploy/check-auto-deploy.py")
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class ReleaseOrdering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="release-order-", dir=ROOT / ".tmp")
        cls.history = Path(cls.temporary.name)
        cls.git("init", "-q")
        cls.base = cls.commit("base")
        cls.middle = cls.commit("middle")
        cls.newest = cls.commit("newest")
        cls.git("checkout", "-q", "--detach", cls.base)
        cls.fork = cls.commit("fork")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=cls.history, text=True,
        ).strip()

    @classmethod
    def commit(cls, name):
        cls.git("commit", "-q", "--allow-empty", "-m", name)
        return cls.git("rev-parse", "HEAD")

    def setUp(self):
        self.images = {}
        self.api_failed = False
        self.environment = patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "PROJECT": "cheese"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def command(self, *args):
        if args[:3] == ("docker", "ps", "-q"):
            service = args[-1].rsplit("=", 1)[-1]
            return "\n".join(name for name in self.images if name.startswith(service))
        if args[:2] == ("docker", "inspect"):
            if args[3] == "{{.Config.Image}}":
                return self.images[args[-1]]
            # Retagging an unchanged image leaves this old OCI revision intact.
            return self.base
        if args[:2] == ("gh", "api"):
            if self.api_failed:
                raise subprocess.CalledProcessError(1, args)
            base, head = args[2].rsplit("/", 1)[-1].split("...")
            base, head = self.git("rev-parse", base), self.git("rev-parse", head)
            if base == head:
                return "identical"
            ancestor = self.git("merge-base", base, head)
            return "ahead" if ancestor == base else "behind" if ancestor == head else "diverged"
        raise AssertionError(args)

    def check(self, candidate):
        with patch.object(GUARD, "command", side_effect=self.command):
            return GUARD.should_skip(candidate)

    def test_late_old_build_cannot_roll_back_newer_running_release(self):
        self.images = {"backend": f"registry/backend:{self.newest[:7]}", "frontend": f"registry/frontend:{self.newest[:7]}"}
        self.assertTrue(self.check(self.middle))

    def test_newer_build_can_advance_both_services(self):
        self.images = {"backend": f"registry/backend:{self.base[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.assertFalse(self.check(self.newest))

    def test_partial_rollout_protects_the_newer_service_and_successor(self):
        self.images = {"backend": f"registry/backend:{self.base[:7]}", "frontend": f"registry/frontend:{self.base[:7]}", "frontend-next": f"registry/frontend:{self.newest[:7]}"}
        self.assertTrue(self.check(self.middle))

    def test_same_release_can_be_retried(self):
        self.images = {"backend": f"registry/backend:{self.middle[:7]}"}
        self.assertFalse(self.check(self.middle))

    def test_first_deployment_needs_no_previous_release(self):
        self.api_failed = True
        self.assertFalse(self.check(self.newest))

    def test_unrelated_candidate_fails_closed(self):
        self.images = {"backend": f"registry/backend:{self.newest[:7]}"}
        with self.assertRaisesRegex(ValueError, "not a descendant"):
            self.check(self.fork)

    def test_registry_tag_is_required_instead_of_an_oci_revision_fallback(self):
        self.images = {"backend": "registry/backend:latest"}
        with self.assertRaisesRegex(ValueError, "image tag"):
            self.check(self.newest)

    def test_api_failure_does_not_allow_a_release(self):
        self.images = {"backend": f"registry/backend:{self.middle[:7]}"}
        self.api_failed = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.check(self.newest)

    def test_manual_rollback_bypasses_policy_even_without_its_script(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        step = next(step for step in workflow["jobs"]["deploy"]["steps"] if step.get("id") == "release")
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            output = Path(directory) / "output"
            environment = {**os.environ, "HOME": directory, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_OUTPUT": str(output)}
            result = subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=directory, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), "skip=false\n")

    def test_skipped_release_stays_skipped_before_candidate_checkout(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        step = next(step for step in workflow["jobs"]["deploy"]["steps"] if step.get("id") == "scope")
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            output = Path(directory) / "output"
            environment = {**os.environ, "RELEASE_SKIP": "true", "GITHUB_OUTPUT": str(output)}
            result = subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=directory, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), "skip=true\n")
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
