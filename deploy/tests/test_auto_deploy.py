#!/usr/bin/env python3
"""Exercise release ordering against a real commit graph and a fake Docker host."""

import importlib.util
import io
import json
import os
import ssl
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
        self.environment = patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "PROJECT": "cheese"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def command(self, *args):
        if args[:3] == ("docker", "ps", "-q"):
            # A container here is named for its compose service; a `-next`
            # one-off carries the service it was run from.
            service = args[-1].rsplit("=", 1)[-1]
            return "\n".join(name for name in self.images if name.removesuffix("-next") == service)
        if args[:2] == ("docker", "inspect"):
            if args[3] == "{{.Config.Image}}":
                return self.images[args[-1]]
            if ".State.Health" in args[3]:
                return self.health.get(args[-1], "healthy")
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

    def check(self, candidate, rebuilt=False):
        with patch.object(GUARD, "command", side_effect=self.command):
            return GUARD.should_skip(candidate, rebuilt=rebuilt)

    def test_a_rebuild_of_the_running_release_is_released_again(self):
        # desktop.yml rebuilds images under a tag dev may already run, to ship
        # new installers; the healthy box runs the images being replaced.
        self.images = {"backend": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.assertFalse(self.check(self.middle, rebuilt=True))

    def test_a_rebuild_still_cannot_roll_back_a_newer_release(self):
        self.images = {"backend": f"registry/backend:{self.newest[:7]}", "frontend": f"registry/frontend:{self.newest[:7]}"}
        self.assertTrue(self.check(self.middle, rebuilt=True))

    def test_late_old_build_cannot_roll_back_newer_running_release(self):
        self.images = {"backend": f"registry/backend:{self.newest[:7]}", "frontend": f"registry/frontend:{self.newest[:7]}"}
        self.assertTrue(self.check(self.middle))

    def test_newer_build_can_advance_both_services(self):
        self.images = {"backend": f"registry/backend:{self.base[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.assertFalse(self.check(self.newest))

    def test_partial_rollout_protects_the_newer_service_and_successor(self):
        self.images = {"backend": f"registry/backend:{self.base[:7]}", "frontend": f"registry/frontend:{self.base[:7]}", "frontend-next": f"registry/frontend:{self.newest[:7]}"}
        self.assertTrue(self.check(self.middle))

    def test_a_release_serving_from_the_second_slot_is_still_protected(self):
        self.images = {"backend-b": f"registry/backend:{self.newest[:7]}", "frontend-b": f"registry/frontend:{self.newest[:7]}"}
        self.assertTrue(self.check(self.middle))

    def test_a_healthy_release_in_the_second_slot_is_not_restarted(self):
        self.images = {"backend-b": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
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

    def test_rebuilt_dispatch_takes_the_policy(self):
        # desktop.yml dispatches naming the commit it rebuilt; that release must
        # run the guard, not take the manual bypass. Without the policy script
        # in the working directory, running the guard fails.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        step = next(step for step in workflow["jobs"]["deploy"]["steps"] if step.get("id") == "release")
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            output = Path(directory) / "output"
            environment = {**os.environ, "HOME": directory, "GITHUB_EVENT_NAME": "workflow_dispatch",
                           "REBUILT": "a" * 40, "CANDIDATE_SHA": "a" * 40, "GITHUB_OUTPUT": str(output)}
            result = subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=directory, env=environment, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("check-auto-deploy.py", result.stderr)
            self.assertFalse(output.exists() and output.read_text())

    def test_skipped_release_stays_skipped_before_candidate_checkout(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        checkout = next(step for step in workflow["jobs"]["deploy"]["steps"]
                        if step.get("name", "").startswith("Check out the built commit"))
        self.assertEqual(checkout["if"], "steps.release.outputs.skip != 'true'")


class Clock:
    """Stands in for the time module: sleeping advances it instantly."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class CandidateCI(unittest.TestCase):
    candidate = "a" * 40

    def setUp(self):
        self.clock = Clock()
        patcher = patch.object(GUARD, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_record(self, **changes):
        return {"id": 10, "head_sha": self.candidate, "head_branch": "main",
                "updated_at": "2026-09-22T12:00:00Z",
                "head_repository": {"full_name": "example/app"}, "event": "push",
                "status": "completed", "conclusion": "success", **changes}

    def queue_record(self, **changes):
        """Required CI as the merge queue reports it for the commit it lands."""
        return self.run_record(**{"event": "merge_group",
                                  "head_branch": f"gh-readonly-queue/main/pr-7-{'b' * 40}", **changes})

    def ready(self, build, ci):
        def workflow_runs(_repository, workflow, _candidate):
            return build if workflow == "build.yml" else ci
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app"}), patch.object(GUARD, "workflow_runs", side_effect=workflow_runs):
            return GUARD.ci_ready(self.candidate)

    def test_workflow_runs_use_authenticated_http_and_read_all_pages(self):
        first = [{"id": number} for number in range(100)]
        second = [{"id": 100}]
        responses = [io.BytesIO(json.dumps({"workflow_runs": page}).encode())
                     for page in (first, second)]
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), patch.object(
            GUARD, "urlopen", side_effect=responses
        ) as urlopen:
            runs = GUARD.workflow_runs("example/app", "build.yml", self.candidate)
        self.assertEqual(len(runs), 101)
        self.assertEqual(urlopen.call_count, 2)
        for number, call in enumerate(urlopen.call_args_list, start=1):
            request = call.args[0]
            self.assertIn(f"page={number}", request.full_url)
            self.assertIn(f"head_sha={self.candidate}", request.full_url)
            self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    def github(self, *answers):
        """A GitHub API that gives each answer in turn: an exception is raised,
        anything else is the JSON body. Returns the patcher and the URLs read."""
        reads = []

        def urlopen(request, timeout):
            reads.append(request.full_url)
            answer = answers[min(len(reads), len(answers)) - 1]
            if isinstance(answer, BaseException):
                raise answer
            return io.BytesIO(json.dumps(answer).encode())
        return patch.object(GUARD, "urlopen", side_effect=urlopen), reads

    def test_a_dropped_connection_is_read_again(self):
        # Deploy run 37667147156 failed its metering release on exactly this,
        # after the app had already been released.
        dropped = GUARD.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING]"))
        build = {"workflow_runs": [self.run_record()]}
        ci = {"workflow_runs": [self.queue_record()]}
        github, reads = self.github(dropped, build, ci)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t"}), \
                patch.object(GUARD.sys, "argv", ["check-auto-deploy.py", "--require-ci", self.candidate]), github:
            GUARD.main()
        self.assertEqual(len(reads), 3)
        self.assertEqual(reads[0], reads[1])

    def test_a_connection_that_keeps_dropping_still_stops_the_release(self):
        github, reads = self.github(ConnectionResetError("reset by peer"))
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t"}), github:
            with self.assertRaises(ConnectionResetError):
                GUARD.ci_ready(self.candidate)
        self.assertEqual(len(reads), 3)

    def test_a_refusal_is_not_read_again(self):
        refused = GUARD.HTTPError("https://api.github.com/x", 403, "rate limited", {}, io.BytesIO(b""))
        github, reads = self.github(refused)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t"}), github:
            with self.assertRaises(GUARD.HTTPError):
                GUARD.ci_ready(self.candidate)
        self.assertEqual(len(reads), 1)

    def test_a_server_error_is_read_again(self):
        failed = GUARD.HTTPError("https://api.github.com/x", 502, "bad gateway", {}, io.BytesIO(b""))
        github, reads = self.github(failed, {"workflow_runs": [self.run_record()]}, {"workflow_runs": [self.queue_record()]})
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t"}), github:
            self.assertTrue(GUARD.ci_ready(self.candidate))
        self.assertEqual(len(reads), 3)

    def test_both_completion_orders_require_both_successes(self):
        done = [self.run_record()]
        pending = [self.run_record(status="in_progress", conclusion=None)]
        queued = [self.queue_record()]
        queue_pending = [self.queue_record(status="in_progress", conclusion=None)]
        self.assertFalse(self.ready(done, queue_pending))
        self.assertFalse(self.ready(pending, queued))
        self.assertTrue(self.ready(done, queued))

    def test_missing_ci_never_deploys(self):
        self.assertFalse(self.ready([self.run_record()], []))

    def test_failed_cancelled_and_skipped_ci_never_deploy(self):
        for conclusion in ("failure", "cancelled", "skipped", "timed_out", "neutral"):
            with self.subTest(conclusion=conclusion):
                self.assertFalse(self.ready([self.run_record()], [self.queue_record(conclusion=conclusion)]))

    def test_a_previous_green_run_cannot_hide_a_new_attempt(self):
        old = self.queue_record(id=9)
        for status, conclusion in (("in_progress", None), ("completed", "failure")):
            self.assertFalse(self.ready([self.run_record()], [self.queue_record(status=status, conclusion=conclusion), old]))

    def test_other_sha_branch_fork_or_pr_success_cannot_authorize_release(self):
        for changes in ({"head_sha": "b" * 40}, {"head_branch": "feature"},
                        {"head_branch": f"gh-readonly-queue/release/pr-7-{'b' * 40}"},
                        {"head_repository": {"full_name": "fork/app"}}, {"event": "pull_request"},
                        {"event": "push", "head_branch": "main"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.ready([self.run_record()], [self.queue_record(**changes)]))

    def test_rerunning_an_older_run_cannot_hide_its_failure_behind_a_newer_id(self):
        rerun = self.run_record(id=9, updated_at="2026-09-22T13:00:00Z", conclusion="failure")
        self.assertFalse(self.ready([self.run_record(), rerun], [self.queue_record()]))

    def test_another_in_progress_attempt_blocks_a_completed_success(self):
        pending = self.run_record(id=9, updated_at="2026-09-22T11:00:00Z", status="in_progress", conclusion=None)
        self.assertFalse(self.ready([self.run_record(), pending], [self.queue_record()]))

    def test_api_failure_stops_eligibility(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app"}), patch.object(GUARD, "workflow_runs", side_effect=OSError("API unavailable")):
            with self.assertRaises(OSError):
                GUARD.ci_ready(self.candidate)

    def eligibility(self, answers):
        """Run the eligibility entry point while each read of the run lists
        gives the next of `answers`, then keeps giving the last one. Returns
        the output and the time of each read."""
        reads = []

        def workflow_runs(_repository, workflow, _candidate):
            if workflow == "build.yml":
                reads.append(self.clock.now)
            build, ci = answers[min(len(reads) - 1, len(answers) - 1)]
            return build if workflow == "build.yml" else ci
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GITHUB_OUTPUT": str(output)}), \
                    patch.object(GUARD.sys, "argv", ["check-auto-deploy.py", "--ci-only", self.candidate]), \
                    patch.object(GUARD, "workflow_runs", side_effect=workflow_runs), \
                    patch.object(GUARD, "newer_on_main", return_value=[]):
                GUARD.main()
            return output.read_text(), reads

    def test_a_run_list_that_still_shows_the_finished_build_running_does_not_refuse(self):
        lagging = ([self.run_record(status="in_progress", conclusion=None)], [self.queue_record()])
        missing = ([], [self.queue_record()])
        settled = ([self.run_record()], [self.queue_record()])
        for stale in (lagging, missing):
            with self.subTest(stale=stale[0]):
                self.clock.now = 0.0
                output, reads = self.eligibility([stale, stale, settled])
                self.assertEqual(output, "ready=true\nsuperseded=\n")
                self.assertLessEqual(reads[-1], 60)

    def test_a_build_that_really_failed_is_refused_within_a_bounded_wait(self):
        failed = ([self.run_record(conclusion="failure")], [self.queue_record()])
        output, reads = self.eligibility([failed])
        self.assertEqual(output, "ready=false\nsuperseded=\n")
        self.assertGreater(len(reads), 1)
        self.assertLessEqual(reads[-1], 180)

    def test_workflow_checks_eligibility_before_reserving_deploy_runner(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        self.assertEqual(workflow["jobs"]["deploy"]["needs"], "eligibility")
        self.assertEqual(workflow["jobs"]["deploy"]["if"].strip(),
                         "needs.eligibility.outputs.ready == 'true' && needs.eligibility.outputs.superseded == ''")
        self.assertNotIn("cheese-dev", workflow["jobs"]["eligibility"]["runs-on"])
        self.assertNotIn("concurrency", workflow)
        self.assertEqual(workflow["jobs"]["deploy"]["concurrency"]["group"], "deploy-dev")
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(set(triggers["workflow_run"]["workflows"]), {"Build and Push Docker Image"})

    def test_required_ci_runs_once_per_commit_in_the_merge_queue(self):
        required = yaml.safe_load((ROOT / ".github/workflows/required-ci.yml").read_text())
        triggers = required.get("on", required.get(True))
        self.assertNotIn("push", triggers)
        self.assertIn("merge_group", triggers)

    def test_ci_rerun_while_waiting_for_deploy_runner_fails_the_release(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                GUARD.sys, "argv", ["check-auto-deploy.py", self.candidate]
            ), patch.object(GUARD, "ci_ready", return_value=False), patch.object(GUARD, "should_skip") as deploy:
                with self.assertRaises(SystemExit) as refused:
                    GUARD.main()
            self.assertIn(self.candidate, str(refused.exception.code))
            self.assertFalse(output.exists())
            deploy.assert_not_called()

    @staticmethod
    def fake_bin(directory, **programs):
        for name, body in programs.items():
            path = Path(directory) / name
            path.write_text(f"#!/usr/bin/env bash\n{body}\n")
            path.chmod(0o755)
        return f"{directory}:{os.environ['PATH']}"

    @staticmethod
    def step(job, name):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        return next(step for step in workflow["jobs"][job]["steps"] if step.get("name") == name)

    def test_a_refused_release_fails_the_run_and_says_what_dev_runs(self):
        step = self.step("eligibility", "Fail when this commit will not be deployed")
        self.assertEqual(step["if"], "steps.check.outputs.ready != 'true'")
        live = "b" * 40
        for curl, expected in ((f"echo '{{\"data\": {{\"sha\": \"{live}\"}}}}'", live), ("exit 7", "unknown")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                summary = Path(directory) / "summary"
                result = subprocess.run(
                    ["bash", "-e", "-c", step["run"]], capture_output=True, text=True,
                    env={**os.environ, "PATH": self.fake_bin(directory, curl=curl),
                         "CANDIDATE_SHA": self.candidate, "GITHUB_STEP_SUMMARY": str(summary)})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"::error::Not deployed {self.candidate}", result.stdout)
                self.assertIn(f"Not deployed `{self.candidate}`", summary.read_text())
                self.assertIn(f"dev runs `{expected}`", summary.read_text())

    def test_every_deploy_run_names_the_commit_dev_runs(self):
        step = self.step("deploy", "Say which commit dev runs")
        self.assertEqual(step["if"], "always()")
        docker = ('case "$1" in ps) echo "backend-b cheese-backend-b-1";; '
                  'inspect) echo "ghcr.io/example/backend:abc1234";; esac')
        for outcome, skip, expected in (
            ("success", "false", f"Deployed `{self.candidate}`. dev runs `abc1234`."),
            ("success", "true", f"Not deployed `{self.candidate}`: dev already runs the same release or a newer one. dev runs `abc1234`."),
            ("failure", "", f"Not deployed `{self.candidate}`: this job ended in failure. dev runs `abc1234`."),
        ):
            with self.subTest(outcome=outcome, skip=skip), tempfile.TemporaryDirectory() as directory:
                summary = Path(directory) / "summary"
                result = subprocess.run(
                    ["bash", "-e", "-c", step["run"]], cwd=ROOT, capture_output=True, text=True,
                    env={**os.environ, "PATH": self.fake_bin(directory, docker=docker),
                         "HOME": directory, "CANDIDATE_SHA": self.candidate, "SKIP": skip,
                         "OUTCOME": outcome, "GITHUB_STEP_SUMMARY": str(summary)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(summary.read_text().strip(), expected)

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
        for job in parent["jobs"].values():
            if "uses" not in job:
                continue
            child = yaml.safe_load((ROOT / job["uses"]).read_text())
            triggers = child.get("on", child.get(True))
            with self.subTest(workflow=job["uses"]):
                self.assertIn("workflow_call", triggers)
                if job["uses"] == "./.github/workflows/remote-execution.yml":
                    self.assertEqual(triggers["push"]["branches"], ["experiment/remote-execution"])
                    self.assertNotIn("pull_request", triggers)
                else:
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




class SupersededRelease(unittest.TestCase):
    """Only one deploy waits for the deploy-dev group, and a newly waiting one
    cancels the one already waiting. A late build of an older commit must not
    take that place from a newer commit's deploy: run 37646390290 (83be7802c,
    built last) cancelled the waiting 37646273755 (64a9aa54e, newer), and main's
    newest commit stayed off dev."""

    older, candidate, newer, newest = ("1" * 40, "2" * 40, "3" * 40, "4" * 40)

    def setUp(self):
        patcher = patch.object(GUARD, "time", Clock())
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def runs(sha, build="success", ci="success"):
        def record(workflow, conclusion, event, branch):
            status = "completed" if conclusion else "in_progress"
            return {"id": 1, "head_sha": sha, "head_branch": branch, "event": event,
                    "updated_at": "2026-10-07T15:43:00Z", "status": status, "conclusion": conclusion,
                    "head_repository": {"full_name": "example/app"}, "workflow": workflow}
        return [record("build.yml", build, "push", "main"),
                record("required-ci.yml", ci, "merge_group", f"gh-readonly-queue/main/pr-1-{sha}")]

    def eligibility(self, on_main, records):
        """`on_main`: main's commits after the candidate, oldest first.
        `records`: each commit's runs. Answers the GitHub API like GitHub."""
        def urlopen(request, timeout):
            url = request.full_url
            if "/compare/" in url:
                body = {"status": "ahead" if on_main else "identical",
                        "commits": [{"sha": sha} for sha in on_main]}
            else:
                workflow = url.split("/actions/workflows/", 1)[1].split("/", 1)[0]
                sha = url.split("head_sha=", 1)[1].split("&", 1)[0]
                body = {"workflow_runs": [run for run in records.get(sha, []) if run["workflow"] == workflow]}
            return io.BytesIO(json.dumps(body).encode())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t",
                                         "GITHUB_OUTPUT": str(output)}), \
                    patch.object(GUARD.sys, "argv", ["check-auto-deploy.py", "--ci-only", self.candidate]), \
                    patch.object(GUARD, "urlopen", side_effect=urlopen), \
                    patch("sys.stdout", new_callable=io.StringIO) as printed:
                GUARD.main()
            return output.read_text(), printed.getvalue()

    def test_an_older_commit_built_after_a_newer_ready_one_steps_aside(self):
        output, printed = self.eligibility([self.newer], {self.candidate: self.runs(self.candidate),
                                                          self.newer: self.runs(self.newer)})
        self.assertEqual(output, f"ready=true\nsuperseded={self.newer}\n")
        self.assertIn(f"Not deploying {self.candidate}: {self.newer}, newer on main", printed)

    def test_the_newest_commit_that_is_ready_is_the_one_named(self):
        output, _ = self.eligibility([self.newer, self.newest], {
            self.candidate: self.runs(self.candidate), self.newer: self.runs(self.newer),
            self.newest: self.runs(self.newest, build=None)})
        self.assertEqual(output, f"ready=true\nsuperseded={self.newer}\n")

    def test_a_newer_commit_that_cannot_be_released_yet_does_not_supersede(self):
        for build, ci in ((None, "success"), ("failure", "success"), ("success", "failure")):
            with self.subTest(build=build, ci=ci):
                output, printed = self.eligibility([self.newer], {
                    self.candidate: self.runs(self.candidate), self.newer: self.runs(self.newer, build=build, ci=ci)})
                self.assertEqual(output, "ready=true\nsuperseded=\n")
                # A newer commit's state is not this release's refusal.
                self.assertNotIn(f"Not deploying {self.newer}", printed)

    def test_main_newest_commit_deploys(self):
        output, _ = self.eligibility([], {self.candidate: self.runs(self.candidate)})
        self.assertEqual(output, "ready=true\nsuperseded=\n")

    def test_a_superseded_run_does_not_ask_for_the_deploy_group(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        self.assertEqual(workflow["jobs"]["eligibility"]["outputs"]["superseded"],
                         "${{ steps.check.outputs.superseded }}")
        condition = workflow["jobs"]["deploy"]["if"]
        self.assertIn("needs.eligibility.outputs.superseded == ''", condition)
        self.assertIn("needs.eligibility.outputs.ready == 'true'", condition)
        step = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                    if step.get("if") == "steps.check.outputs.superseded != ''")
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary"
            result = subprocess.run(["bash", "-e", "-c", step["run"]], capture_output=True, text=True,
                                    env={**os.environ, "CANDIDATE_SHA": self.candidate, "NEWER": self.newer,
                                         "GITHUB_STEP_SUMMARY": str(summary)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"`{self.candidate}`", summary.read_text())
            self.assertIn(f"`{self.newer}`", summary.read_text())

class DesktopRebuild(unittest.TestCase):
    """desktop.yml rebuilds the images for new installers and asks deploy-dev to
    release them. main can move while the rebuild runs, so the release must be
    of the commit the rebuild built."""

    built, tip = "b" * 40, "c" * 40

    FAKE_GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE/gh.log"
case "$1 $2" in
  "run list") echo 77 ;;
  "run view") [ "$3" = 77 ] && echo "$BUILT" ;;
  "workflow run") [ "$3" = build.yml ] && echo "$TIP" > "$FAKE/main" ;;
esac
exit 0
"""

    def test_the_deploy_it_asks_for_is_of_the_commit_it_rebuilt(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text())
        step = next(step for step in workflow["jobs"]["publish"]["steps"]
                    if "deploy-dev.yml" in step.get("run", ""))
        # The run number is the only expression in the script.
        script = step["run"].replace("${{ github.run_number }}", "7")
        self.assertNotIn("${{", script)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "bin").mkdir()
            (work / "bin/gh").write_text(self.FAKE_GH)
            (work / "bin/gh").chmod(0o755)
            (work / "out").mkdir()
            for name in ("Cheese-Setup-x64.exe.sig", "Cheese-arm64.app.tar.gz.sig", "Cheese-x64.app.tar.gz.sig"):
                (work / "out" / name).write_text("signature")
            result = subprocess.run(
                ["bash", "-e", "-c", script], cwd=work, capture_output=True, text=True, timeout=60,
                env={**os.environ, "PATH": f"{work / 'bin'}:{os.environ['PATH']}", "FAKE": str(work),
                     "BUILT": self.built, "TIP": self.tip, "GH_REPO": "example/app"})
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (work / "gh.log").read_text().splitlines()
        self.assertEqual(calls[-1], f"workflow run deploy-dev.yml --ref main -f rebuilt={self.built}")

    def test_every_part_of_a_deploy_run_names_the_same_commit(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        expressions = {workflow["run-name"].removeprefix("Deploy ")}
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "CANDIDATE_SHA" in step.get("env", {}):
                    expressions.add(step["env"]["CANDIDATE_SHA"])
                if step.get("name", "").startswith("Check out the built commit"):
                    expressions.add(step["with"]["ref"])
        self.assertEqual(expressions, {"${{ inputs.rebuilt || github.event.workflow_run.head_sha || github.sha }}"})
        self.assertIn("rebuilt", workflow[True]["workflow_dispatch"]["inputs"])

    def test_a_rebuilt_dispatch_checks_its_images_and_tests(self):
        # Without the policy script in the working directory, the check fails:
        # proof that a rebuilt dispatch does not take the manual bypass.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        step = next(step for step in workflow["jobs"]["eligibility"]["steps"] if step.get("id") == "check")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = subprocess.run(
                ["bash", "-eu", "-c", step["run"]], cwd=directory, capture_output=True, text=True,
                env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "REBUILT": self.built,
                     "CANDIDATE_SHA": self.built, "GITHUB_OUTPUT": str(output)})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("check-auto-deploy.py", result.stderr)
            self.assertFalse(output.exists() and output.read_text())

if __name__ == "__main__":
    unittest.main()
