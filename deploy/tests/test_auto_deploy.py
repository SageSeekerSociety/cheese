#!/usr/bin/env python3
"""Exercise release ordering against a real commit graph and a fake Docker host."""

import importlib.util
import io
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
        self.health = {}
        self.environment = patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "example/app", "PROJECT": "cheese", "GH_TOKEN": "test-token",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def command(self, *args):
        if args[:3] == ("docker", "ps", "-q"):
            service = args[-1].rsplit("=", 1)[-1]
            return "\n".join(name for name in self.images if name.startswith(service))
        if args[:2] == ("docker", "inspect"):
            if args[3] == "{{.Config.Image}}":
                return self.images[args[-1]]
            if ".State.Health" in args[3]:
                return self.health.get(args[-1], "healthy")
            # Retagging an unchanged image leaves this old OCI revision intact.
            return self.base
        raise AssertionError(args)

    def github_api(self, path, params=None):
        if self.api_failed:
            raise OSError("GitHub API unavailable")
        if "/compare/" in path:
            base, head = path.rsplit("/", 1)[-1].split("...")
            base, head = self.git("rev-parse", base), self.git("rev-parse", head)
            if base == head:
                status = "identical"
            else:
                ancestor = self.git("merge-base", base, head)
                status = "ahead" if ancestor == base else "behind" if ancestor == head else "diverged"
            return {"status": status}
        return {"workflow_runs": []}

    def check(self, candidate):
        with patch.object(GUARD, "command", side_effect=self.command), patch.object(
            GUARD, "github_api", side_effect=self.github_api
        ):
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

    def test_duplicate_completion_event_does_not_restart_healthy_release(self):
        self.images = {"backend": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.assertTrue(self.check(self.middle))

    def test_failed_same_release_can_be_retried(self):
        self.images = {"backend": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.health = {"backend": "unhealthy"}
        self.assertFalse(self.check(self.middle))

    def test_docs_after_queued_code_still_deploys_relative_to_running_release(self):
        self.git("checkout", "-q", "--detach", self.base)
        (self.history / "app.py").write_text("print('new release')\n")
        self.git("add", "app.py")
        code = self.commit("queued code")
        (self.history / "README.md").write_text("Updated documentation\n")
        self.git("add", "README.md")
        docs = self.commit("docs after queued code")
        self.assertEqual(self.git("diff", "--name-only", code, docs), "README.md")
        self.assertIn("app.py", self.git("diff", "--name-only", self.base, docs))
        self.images = {"backend": f"registry/backend:{self.base[:7]}",
                       "frontend": f"registry/frontend:{self.base[:7]}"}
        self.assertFalse(self.check(docs))
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        for step in workflow["jobs"]["deploy"]["steps"]:
            self.assertNotEqual(step.get("id"), "scope")
            if step.get("name") == "Log in to ghcr":
                self.assertEqual(step["if"], "steps.release.outputs.skip != 'true'")

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
        with self.assertRaises(OSError):
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
        checkout = next(step for step in workflow["jobs"]["deploy"]["steps"]
                        if step.get("name", "").startswith("Check out the built commit"))
        self.assertEqual(checkout["if"], "steps.release.outputs.skip != 'true'")


class CandidateCI(unittest.TestCase):
    candidate = "a" * 40

    def run_record(self, **changes):
        return {"id": 10, "head_sha": self.candidate, "head_branch": "main",
                "updated_at": "2026-09-22T12:00:00Z",
                "head_repository": {"full_name": "example/app"}, "event": "push",
                "status": "completed", "conclusion": "success", **changes}

    def ready(self, build, ci):
        def github_api(path, params=None):
            records = build if "/build.yml/" in path else ci
            return {"workflow_runs": records}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "test-token"}), patch.object(
            GUARD, "github_api", side_effect=github_api
        ):
            return GUARD.ci_ready(self.candidate)

    def test_both_completion_orders_require_both_successes(self):
        done = [self.run_record()]
        pending = [self.run_record(status="in_progress", conclusion=None)]
        self.assertFalse(self.ready(done, pending))
        self.assertFalse(self.ready(pending, done))
        self.assertTrue(self.ready(done, done))

    def test_missing_ci_never_deploys(self):
        self.assertFalse(self.ready([self.run_record()], []))

    def test_failed_cancelled_and_skipped_ci_never_deploy(self):
        for conclusion in ("failure", "cancelled", "skipped", "timed_out", "neutral"):
            with self.subTest(conclusion=conclusion):
                self.assertFalse(self.ready([self.run_record()], [self.run_record(conclusion=conclusion)]))

    def test_a_previous_green_run_cannot_hide_a_new_attempt(self):
        old = self.run_record(id=9)
        for status, conclusion in (("in_progress", None), ("completed", "failure")):
            self.assertFalse(self.ready([old], [self.run_record(status=status, conclusion=conclusion), old]))

    def test_other_sha_branch_fork_or_pr_success_cannot_authorize_release(self):
        for changes in ({"head_sha": "b" * 40}, {"head_branch": "feature"},
                        {"head_repository": {"full_name": "fork/app"}}, {"event": "pull_request"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.ready([self.run_record()], [self.run_record(**changes)]))

    def test_rerunning_an_older_run_cannot_hide_its_failure_behind_a_newer_id(self):
        rerun = self.run_record(id=9, updated_at="2026-09-22T13:00:00Z", conclusion="failure")
        self.assertFalse(self.ready([self.run_record(), rerun], [self.run_record()]))

    def test_another_in_progress_attempt_blocks_a_completed_success(self):
        pending = self.run_record(id=9, updated_at="2026-09-22T11:00:00Z", status="in_progress", conclusion=None)
        self.assertFalse(self.ready([self.run_record(), pending], [self.run_record()]))

    def test_api_failure_stops_eligibility(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "test-token"}), patch.object(
            GUARD, "github_api", side_effect=OSError("GitHub API unavailable")
        ):
            with self.assertRaises(OSError):
                GUARD.ci_ready(self.candidate)

    def test_github_api_uses_bearer_token_without_a_cli(self):
        response = io.BytesIO(b'{"status":"ahead"}')
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), patch.object(
            GUARD, "urlopen", return_value=response
        ) as open_url:
            self.assertEqual(GUARD.github_api("repos/example/app/compare/base...head"), {"status": "ahead"})
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    def test_workflow_checks_eligibility_before_reserving_deploy_runner(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        self.assertEqual(workflow["jobs"]["deploy"]["needs"], "eligibility")
        self.assertEqual(workflow["jobs"]["deploy"]["if"].strip(), "needs.eligibility.outputs.ready == 'true'")
        self.assertEqual(workflow["jobs"]["eligibility"]["runs-on"], ["self-hosted", "cheese-ci"])
        self.assertNotIn("concurrency", workflow)
        self.assertEqual(workflow["jobs"]["deploy"]["concurrency"]["group"], "deploy-dev")
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(set(triggers["workflow_run"]["workflows"]), {"Build and Push Docker Image", "Required CI"})
        required = yaml.safe_load((ROOT / ".github/workflows/required-ci.yml").read_text())
        self.assertEqual(required.get("on", required.get(True))["push"]["branches"], ["main"])

    def test_ci_rerun_while_waiting_for_deploy_runner_prevents_release(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                GUARD.sys, "argv", ["check-auto-deploy.py", self.candidate]
            ), patch.object(GUARD, "ci_ready", return_value=False), patch.object(GUARD, "should_skip") as deploy:
                GUARD.main()
            self.assertEqual(output.read_text(), "skip=true\n")
            deploy.assert_not_called()

    def test_release_entry_rejects_unready_ci(self):
        for filename, job in (("deploy.yml", "wait-for-ci"), ("deploy-prod.yml", "gate")):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
            gate = next(step for step in workflow["jobs"][job]["steps"]
                        if step.get("name") == "Require completed validation")
            for ready, expected in (("true", 0), ("false", 1), ("", 1)):
                with self.subTest(filename=filename, ready=ready):
                    result = subprocess.run(["bash", "-eu", "-c", gate["run"]],
                                            env={**os.environ, "READY": ready},
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stdout)

    def test_release_pins_the_validated_commit_after_approval(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        jobs = workflow["jobs"]
        checkout = next(step for step in jobs["deploy"]["steps"]
                        if step.get("name") == "Check out the validated release")
        self.assertEqual(checkout["with"]["ref"],
                         "${{ needs.wait-for-ci.outputs.sha || inputs.ref }}")
        self.assertEqual(jobs["wait-for-ci"]["outputs"]["sha"],
                         "${{ steps.candidate.outputs.sha }}")
        ruc = yaml.safe_load((ROOT / ".github/workflows/deploy-prod.yml").read_text())
        resolve = next(step for step in ruc["jobs"]["deploy"]["steps"]
                       if step.get("id") == "img")
        self.assertEqual(resolve["env"]["REF"], "${{ needs.gate.outputs.sha || inputs.ref }}")
        self.assertEqual(ruc["jobs"]["gate"]["outputs"]["sha"],
                         "${{ steps.candidate.outputs.sha }}")

    def test_main_runs_selected_suites_only_through_required_ci(self):
        workflows = ROOT / ".github/workflows"
        parent = yaml.safe_load((workflows / "required-ci.yml").read_text())
        self.assertEqual(parent.get("on", parent.get(True))["push"]["branches"], ["main"])
        for job in parent["jobs"].values():
            if "uses" not in job:
                continue
            child = yaml.safe_load((ROOT / job["uses"]).read_text())
            triggers = child.get("on", child.get(True))
            with self.subTest(workflow=job["uses"]):
                self.assertIn("workflow_call", triggers)
                self.assertNotIn("push", triggers)
        for filename in ("harness-contract.yml", "mcp-contract.yml"):
            child = yaml.safe_load((workflows / filename).read_text())
            self.assertIn("workflow_dispatch", child.get("on", child.get(True)))
            self.assertIn("github.run_id", child["concurrency"]["group"])
            self.assertEqual(child["concurrency"]["cancel-in-progress"],
                             "${{ github.event_name == 'pull_request' }}")
        mcp = yaml.safe_load((workflows / "mcp-contract.yml").read_text())
        self.assertIn("schedule", mcp.get("on", mcp.get(True)))

    def test_failed_rerun_during_approval_blocks_release(self):
        for ready in (False, True):
            with self.subTest(ready=ready), patch.object(
                GUARD.sys, "argv", ["check-auto-deploy.py", "--require-ci", self.candidate]
            ), patch.object(GUARD, "ci_ready", return_value=ready):
                if ready:
                    GUARD.main()
                else:
                    with self.assertRaises(SystemExit):
                        GUARD.main()
        for filename in ("deploy.yml", "deploy-prod.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
            steps = workflow["jobs"]["deploy"]["steps"]
            gate = next(step for step in steps if step.get("name") == "Recheck validation after approval")
            self.assertEqual(gate["if"], "github.event_name == 'release'")
            self.assertIn("--require-ci", gate["run"])


if __name__ == "__main__":
    unittest.main()
