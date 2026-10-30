#!/usr/bin/env python3
"""Exercise release ordering against a real commit graph and a fake Docker host."""

import importlib.util
import io
import json
import os
import shutil
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


class Clock:
    """Stands in for the time module: sleeping advances it instantly."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


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
        self.health = {}
        self.readyz = {}
        self.compare_errors = []
        self.reads = []
        self.environment = patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "PROJECT": "cheese",
                                                   "GH_TOKEN": "test-token"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.clock = patch.object(GUARD, "time", Clock())
        self.clock.start()
        self.addCleanup(self.clock.stop)

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
        if args[:2] == ("docker", "exec"):
            answer = self.readyz.get(args[2])
            if answer is None:
                raise subprocess.CalledProcessError(7, args)
            return answer if isinstance(answer, str) else json.dumps(answer)
        raise AssertionError(args)

    def compare(self, request, timeout):
        """GitHub's compare endpoint, answered from the real commit graph."""
        self.reads.append(request.full_url)
        if self.compare_errors:
            raise self.compare_errors.pop(0)
        base, head = request.full_url.rsplit("/", 1)[-1].split("...")
        base, head = self.git("rev-parse", base), self.git("rev-parse", head)
        if base == head:
            status = "identical"
        else:
            ancestor = self.git("merge-base", base, head)
            status = "ahead" if ancestor == base else "behind" if ancestor == head else "diverged"
        return io.BytesIO(json.dumps({"status": status}).encode())

    def check(self, candidate, rebuilt=False):
        with patch.object(GUARD, "command", side_effect=self.command), \
                patch.object(GUARD, "urlopen", side_effect=self.compare):
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

    def test_same_release_is_not_redeployed_over_a_dependency_outage(self):
        """Unready only because Postgres or Redis is out of reach: the same build
        released again would roll back and add a failed run, nothing more."""
        self.images = {"backend": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.health = {"backend": "unhealthy"}
        self.readyz = {"backend": {"status": "unready", "unready": ["database", "redis"]}}
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertTrue(self.check(self.middle))
        self.assertIn("database, redis is out of reach", out.getvalue())

    def test_same_release_is_retried_when_the_build_itself_is_unready(self):
        self.images = {"backend": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.health = {"backend": "unhealthy"}
        for answer in (
            {"status": "unready", "unready": ["database", "routes"]},
            {"status": "unready", "unready": []},
            "<html>502</html>",
            None,
        ):
            with self.subTest(answer=answer):
                self.readyz = {"backend": answer}
                self.assertFalse(self.check(self.middle))

    def test_a_dependency_outage_does_not_hide_an_unhealthy_frontend(self):
        self.images = {"backend": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.health = {"backend": "unhealthy", "frontend": "unhealthy"}
        self.readyz = {"backend": {"status": "unready", "unready": ["redis"]}}
        self.assertFalse(self.check(self.middle))

    def test_a_newer_build_still_deploys_over_a_dependency_outage(self):
        self.images = {"backend": f"registry/backend:{self.middle[:7]}", "frontend": f"registry/frontend:{self.middle[:7]}"}
        self.health = {"backend": "unhealthy"}
        self.readyz = {"backend": {"status": "unready", "unready": ["database"]}}
        self.assertFalse(self.check(self.newest))

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
        # With no running containers there is nothing to compare against, so a
        # bootstrap asks GitHub nothing.
        self.assertFalse(self.check(self.newest))
        self.assertEqual(self.reads, [])

    def test_unrelated_candidate_fails_closed(self):
        self.images = {"backend": f"registry/backend:{self.newest[:7]}"}
        with self.assertRaisesRegex(ValueError, "not a descendant"):
            self.check(self.fork)

    def test_registry_tag_is_required_instead_of_an_oci_revision_fallback(self):
        self.images = {"backend": "registry/backend:latest"}
        with self.assertRaisesRegex(ValueError, "image tag"):
            self.check(self.newest)

    def test_a_dropped_compare_read_is_read_again(self):
        # Deploy run 37710891132: the comparison against the running release
        # dropped its connection once and the whole job failed.
        self.compare_errors = [GUARD.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING]"))]
        self.images = {"backend": f"registry/backend:{self.base[:7]}"}
        self.assertFalse(self.check(self.middle))
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(self.reads[0], self.reads[1])

    def test_a_compare_that_keeps_dropping_still_stops_the_release(self):
        self.compare_errors = [ConnectionResetError("reset by peer")] * 3
        self.images = {"backend": f"registry/backend:{self.base[:7]}"}
        with self.assertRaises(ConnectionResetError):
            self.check(self.middle)
        self.assertEqual(len(self.reads), 3)

    def test_manual_rollback_bypasses_policy_even_without_its_script(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        step = next(step for step in workflow["jobs"]["deploy"]["steps"] if step.get("id") == "release")
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            output = Path(directory) / "output"
            environment = {**os.environ, "HOME": directory, "GITHUB_EVENT_NAME": "workflow_dispatch",
                           "ACTOR": "wangchangxin", "REF_INPUT": "main", "GITHUB_OUTPUT": str(output)}
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

    def gate_record(self, **changes):
        """Required CI as the main push reports it for the commit it lands."""
        return self.run_record(**changes)

    def ready(self, build, ci, covered=False):
        def workflow_runs(_repository, workflow, _candidate):
            return build if workflow == "build.yml" else ci
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app"}), \
                patch.object(GUARD, "workflow_runs", side_effect=workflow_runs), \
                patch.object(GUARD, "covered_by_green", return_value=covered):
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
        ci = {"workflow_runs": [self.gate_record()]}
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

    def require_ci(self, *answers):
        """Run the post-approval recheck against `answers`; return what it wrote
        to GITHUB_OUTPUT and the exception it stopped with."""
        github, _reads = self.github(*answers)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t",
                                         "GITHUB_OUTPUT": str(output)}), \
                    patch.object(GUARD.sys, "argv", ["check-auto-deploy.py", "--require-ci", self.candidate]), \
                    github, self.assertRaises(BaseException) as stopped:
                GUARD.main()
            return (output.read_text() if output.exists() else ""), stopped.exception

    def test_a_release_whose_validation_failed_says_it_was_refused(self):
        """The deploy alert stays quiet on a gate's refusal and pages on anything else
        that fails this step; this output is how it tells the two apart."""
        failed = {"workflow_runs": [self.gate_record(conclusion="failure")]}
        written, stopped = self.require_ci({"workflow_runs": [self.run_record()]}, failed)
        self.assertIsInstance(stopped, SystemExit)
        self.assertIn("refused=true", written)

    def test_a_guard_that_crashed_does_not_say_it_refused(self):
        """Deploy run 37710891132 failed in this step on a GitHub read, not a verdict."""
        written, stopped = self.require_ci(ConnectionResetError("reset by peer"))
        self.assertIsInstance(stopped, ConnectionResetError)
        self.assertNotIn("refused", written)

    def test_a_refusal_is_not_read_again(self):
        refused = GUARD.HTTPError("https://api.github.com/x", 403, "rate limited", {}, io.BytesIO(b""))
        github, reads = self.github(refused)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t"}), github:
            with self.assertRaises(GUARD.HTTPError):
                GUARD.ci_ready(self.candidate)
        self.assertEqual(len(reads), 1)

    def test_a_server_error_is_read_again(self):
        failed = GUARD.HTTPError("https://api.github.com/x", 502, "bad gateway", {}, io.BytesIO(b""))
        github, reads = self.github(failed, {"workflow_runs": [self.run_record()]}, {"workflow_runs": [self.gate_record()]})
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GH_TOKEN": "t"}), github:
            self.assertTrue(GUARD.ci_ready(self.candidate))
        self.assertEqual(len(reads), 3)

    def test_both_completion_orders_require_both_successes(self):
        done = [self.run_record()]
        pending = [self.run_record(status="in_progress", conclusion=None)]
        queued = [self.gate_record()]
        queue_pending = [self.gate_record(status="in_progress", conclusion=None)]
        self.assertFalse(self.ready(done, queue_pending))
        self.assertFalse(self.ready(pending, queued))
        self.assertTrue(self.ready(done, queued))

    def test_missing_ci_never_deploys(self):
        self.assertFalse(self.ready([self.run_record()], []))

    def test_failed_cancelled_and_skipped_ci_never_deploy(self):
        for conclusion in ("failure", "cancelled", "skipped", "timed_out", "neutral"):
            with self.subTest(conclusion=conclusion):
                self.assertFalse(self.ready([self.run_record()], [self.gate_record(conclusion=conclusion)]))

    def test_a_previous_green_run_cannot_hide_a_new_attempt(self):
        old = self.gate_record(id=9)
        for status, conclusion in (("in_progress", None), ("completed", "failure")):
            self.assertFalse(self.ready([self.run_record()], [self.gate_record(status=status, conclusion=conclusion), old]))

    def test_other_sha_branch_fork_or_pr_success_cannot_authorize_release(self):
        for changes in ({"head_sha": "b" * 40}, {"head_branch": "feature"},
                        {"head_branch": f"gh-readonly-queue/release/pr-7-{'b' * 40}"},
                        {"head_repository": {"full_name": "fork/app"}}, {"event": "pull_request"},
                        {"event": "push", "head_branch": "experiment/remote-execution"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.ready([self.run_record()], [self.gate_record(**changes)]))

    def test_a_direct_main_push_authorizes(self):
        self.assertTrue(self.ready([self.run_record()], [self.run_record()]))

    def gate_run(self, **changes):
        return self.run_record(event="push", head_branch="main", **changes)

    def green(self, *answers):
        github, reads = self.github(*answers)
        with patch.dict(os.environ, {"GH_TOKEN": "t"}), github:
            return GUARD.green_anchor("example/app"), reads

    def test_the_green_anchor_is_read_from_the_branch_endpoint(self):
        # GET /git/ref/{ref} takes `heads/<branch>`; the `refs/heads/` spelling
        # is for the create endpoint and 404s here, which would silently read as
        # "no anchor yet" and disable the batch's coverage.
        anchor, reads = self.green({"object": {"sha": self.candidate}})
        self.assertEqual(anchor, self.candidate)
        self.assertEqual(reads[-1], "https://api.github.com/repos/example/app/git/ref/heads/main-green")

    def test_no_anchor_yet_is_not_an_error(self):
        missing = GUARD.HTTPError("https://api.github.com/x", 404, "Not Found", {}, io.BytesIO(b""))
        anchor, _reads = self.green(missing)
        self.assertEqual(anchor, "")

    def test_a_commit_behind_the_anchor_is_covered(self):
        # compare/{candidate}...{anchor}: "ahead" means the anchor is ahead of
        # this commit, i.e. the green run's diff included it.
        for status, expected in (("ahead", True), ("identical", True),
                                 ("behind", False), ("diverged", False)):
            with self.subTest(status=status):
                github, reads = self.github({"object": {"sha": "b" * 40}}, {"status": status})
                with patch.dict(os.environ, {"GH_TOKEN": "t"}), github:
                    self.assertEqual(GUARD.covered_by_green(self.candidate, "example/app"), expected)
                self.assertIn(f"compare/{self.candidate}...{'b' * 40}", reads[-1])

    def test_no_anchor_covers_nothing(self):
        missing = GUARD.HTTPError("https://api.github.com/x", 404, "Not Found", {}, io.BytesIO(b""))
        github, _reads = self.github(missing)
        with patch.dict(os.environ, {"GH_TOKEN": "t"}), github:
            self.assertFalse(GUARD.covered_by_green(self.candidate, "example/app"))

    def test_a_batch_run_that_was_replaced_is_proved_by_the_later_green_one(self):
        # The concurrency group replaced this commit's push run. A later run
        # covered it and passed, so the commit is validated; its own release
        # then steps aside for that newer commit (superseded_by).
        replaced = [self.gate_run(conclusion="cancelled")]
        self.assertTrue(self.ready([self.run_record()], replaced, covered=True))
        self.assertFalse(self.ready([self.run_record()], replaced, covered=False))

    def test_the_call_site_asks_about_the_candidate_commit(self):
        # Every test above replaces covered_by_green with a constant, so a
        # swapped call-site argument would stay green while in production the
        # anchor were looked up in a repository named after the commit (404 →
        # "no anchor") and no superseded run were ever excused. Drive the real
        # wiring and read the two URLs it asks for.
        for conclusion in ("cancelled", "skipped"):
            with self.subTest(conclusion=conclusion):
                replaced = [self.gate_run(conclusion=conclusion)]
                github, reads = self.github({"object": {"sha": "b" * 40}},
                                            {"status": "ahead"})
                with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app",
                                             "GH_TOKEN": "t"}), \
                        patch.object(GUARD, "workflow_runs", side_effect=lambda _r, workflow, _c: (
                            [self.run_record()] if workflow == "build.yml" else replaced)), github:
                    self.assertTrue(GUARD.ci_ready(self.candidate))
                self.assertEqual(reads[0],
                                 "https://api.github.com/repos/example/app/git/ref/heads/main-green")
                self.assertIn(f"compare/{self.candidate}...{'b' * 40}", reads[1])

    def test_a_run_that_failed_is_not_excused_by_a_later_green_batch(self):
        # Its change did fail here; the later run tested it together with a fix,
        # which is not the same commit to release.
        failed = [self.gate_run(conclusion="failure")]
        self.assertFalse(self.ready([self.run_record()], failed, covered=True))

    def test_a_commit_with_no_run_is_not_proved_by_the_anchor_alone(self):
        # An ancestor of the anchor need not have been inside the run that set
        # it: the first push run diffs from the commit before its push.
        self.assertFalse(self.ready([self.run_record()], [], covered=True))

    def waiting(self, runs, newer=("n" * 40,)):
        """ci_waiting with the gate's runs and the commits after it on main.

        ``newer`` defaults to one commit: a cancelled run is only a wait when
        something newer on main can cover it, and every test here but the
        uncovered one is about that coverable case.
        """
        def workflow_runs(_repository, workflow, _candidate):
            # Answering every workflow with the same list would hide a lookup
            # pointed at the wrong one: build.yml's runs are not the gate.
            if workflow != "required-ci.yml":
                return []
            return runs
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app"}), \
                patch.object(GUARD, "workflow_runs", side_effect=workflow_runs), \
                patch.object(GUARD, "newer_on_main", return_value=list(newer)):
            return GUARD.ci_waiting(self.candidate)

    def test_a_gate_that_has_not_finished_is_waited_for_not_refused(self):
        # Required CI runs on main after the commit lands, so at build
        # completion it is usually still running; its own completion triggers
        # this workflow again and releases the commit.
        for status, conclusion in (("queued", None), ("in_progress", None)):
            with self.subTest(status=status):
                self.assertTrue(self.waiting([self.gate_run(status=status, conclusion=conclusion)]))

    def test_a_queued_run_a_newer_push_superseded_waits_for_the_newer_batch(self):
        # Batched push runs share a concurrency group, so GitHub cancels the
        # older of two queued runs; the newer commit's run covers both.
        self.assertTrue(self.waiting([self.gate_run(conclusion="cancelled")]))

    def test_a_cancelled_run_nothing_newer_covers_is_not_waited_for(self):
        # A manual or infrastructure cancellation, or a newer batch that itself
        # failed: no later run can diff over this commit, so treating it as a
        # wait would leave dev silently behind with no red anywhere.
        self.assertFalse(self.waiting([self.gate_run(conclusion="cancelled")], newer=()))

    def test_a_gate_that_failed_or_never_ran_is_not_waited_for(self):
        for runs in ([self.gate_run(conclusion="failure")],
                     [self.gate_run(conclusion="timed_out")],
                     [self.gate_run(conclusion="success")],
                     [self.gate_record()],
                     []):
            with self.subTest(runs=runs):
                self.assertFalse(self.waiting(runs))

    def test_the_latest_attempt_wins_across_the_two_routes(self):
        # A queue run is one route and a push run the other, but a newer failed
        # attempt on either still means the gate did not pass.
        older = self.gate_record(updated_at="2026-09-22T12:00:00Z")
        newer = self.run_record(event="push", head_branch="main",
                                updated_at="2026-09-22T12:05:00Z", conclusion="failure")
        self.assertFalse(self.ready([self.run_record()], [older, newer]))

    def test_rerunning_an_older_run_cannot_hide_its_failure_behind_a_newer_id(self):
        rerun = self.run_record(id=9, updated_at="2026-09-22T13:00:00Z", conclusion="failure")
        self.assertFalse(self.ready([self.run_record(), rerun], [self.gate_record()]))

    def test_another_in_progress_attempt_blocks_a_completed_success(self):
        pending = self.run_record(id=9, updated_at="2026-09-22T11:00:00Z", status="in_progress", conclusion=None)
        self.assertFalse(self.ready([self.run_record(), pending], [self.gate_record()]))

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
            elif workflow != "required-ci.yml":
                # ci_waiting asks about Required CI; a lookup pointed at any
                # other workflow must not be answered with the gate's runs.
                return []
            build, ci = answers[min(len(reads) - 1, len(answers) - 1)]
            return build if workflow == "build.yml" else ci
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/app", "GITHUB_OUTPUT": str(output)}), \
                    patch.object(GUARD.sys, "argv", ["check-auto-deploy.py", "--ci-only", self.candidate]), \
                    patch.object(GUARD, "workflow_runs", side_effect=workflow_runs), \
                    patch.object(GUARD, "newer_on_main", return_value=[]), \
                    patch.object(GUARD, "covered_by_green", return_value=False):
                GUARD.main()
            return output.read_text(), reads

    def test_a_run_list_that_still_shows_the_finished_build_running_does_not_refuse(self):
        lagging = ([self.run_record(status="in_progress", conclusion=None)], [self.gate_record()])
        missing = ([], [self.gate_record()])
        settled = ([self.run_record()], [self.gate_record()])
        for stale in (lagging, missing):
            with self.subTest(stale=stale[0]):
                self.clock.now = 0.0
                output, reads = self.eligibility([stale, stale, settled])
                self.assertEqual(output, "ready=true\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")
                self.assertLessEqual(reads[-1], 60)

    def test_the_entry_point_waits_for_an_image_build_still_running(self):
        # Required CI finishing before the build is the rare ordering; with no
        # other trigger, giving up would leave the commit unreleased.
        building = ([self.run_record(status="in_progress", conclusion=None)],
                    [self.gate_record()])
        settled = ([self.run_record()], [self.gate_record()])
        output, reads = self.eligibility([building] * 20 + [settled])
        self.assertEqual(output, "ready=true\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")
        # It outlasted the two-minute settle wait, i.e. the build wait ran.
        self.assertGreater(reads[-1], 120)
        self.assertLessEqual(reads[-1], GUARD.BUILD_WAIT_SECONDS + 180)

    def test_a_failed_build_is_not_waited_for(self):
        failed = ([self.run_record(conclusion="failure")], [self.gate_record()])
        output, reads = self.eligibility([failed])
        self.assertEqual(output, "ready=false\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")
        self.assertLessEqual(reads[-1], 180)

    def test_a_build_that_outlasts_the_wait_is_reported_as_still_building(self):
        """The refusal must say the build was still running; minutes later the
        generic "no successful attempt" reason is false and points nowhere."""
        building = ([self.run_record(status="in_progress", conclusion=None)],
                    [self.gate_record()])
        self.clock.now = 0.0
        output, reads = self.eligibility([building])
        self.assertEqual(output, "ready=false\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=true\n")
        # The initial settle (120 s) plus the wait, and no more: a settle inside
        # the wait is capped at the wait's deadline instead of running past it.
        self.assertGreaterEqual(reads[-1], GUARD.BUILD_WAIT_SECONDS)
        self.assertLessEqual(reads[-1], GUARD.BUILD_WAIT_SECONDS + GUARD.SETTLE_SECONDS)

    def test_a_build_that_fails_during_the_wait_is_not_reported_as_still_building(self):
        """`build_pending` is the re-read after the wait, not just "not ready":
        a build that ends as a failure must get the generic refusal."""
        building = ([self.run_record(status="in_progress", conclusion=None)],
                    [self.gate_record()])
        failed = ([self.run_record(conclusion="failure")], [self.gate_record()])
        self.clock.now = 0.0
        output, reads = self.eligibility([building] * 12 + [failed])
        self.assertEqual(output, "ready=false\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")
        self.assertGreater(reads[-1], GUARD.SETTLE_SECONDS)

    def test_a_build_that_really_failed_is_refused_within_a_bounded_wait(self):
        failed = ([self.run_record(conclusion="failure")], [self.gate_record()])
        output, reads = self.eligibility([failed])
        self.assertEqual(output, "ready=false\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")
        self.assertGreater(len(reads), 1)
        self.assertLessEqual(reads[-1], 180)

    def test_the_entry_point_refuses_a_cancelled_gate_nothing_covers(self):
        # The other half of the waiting rule, driven end to end: with nothing
        # newer on main, a cancelled gate is a refusal whose reason the step
        # summary can name, not a wait that never ends.
        cancelled = ([self.run_record()], [self.gate_run(conclusion="cancelled")])
        output, _reads = self.eligibility([cancelled])
        self.assertEqual(output,
                         "ready=false\nsuperseded=\nwaiting=false\nuncovered=true\nbuild_pending=false\n")

    def test_the_entry_point_reports_waiting_while_the_gate_runs(self):
        # The build's completion is usually the first of the two events and the
        # gate is still running then; the whole point of the waiting state is
        # that this entry point says so instead of refusing. Driving only
        # ci_waiting would leave `waiting = not ready and ...` unwatched.
        running = ([self.run_record()],
                   [self.gate_run(status="in_progress", conclusion=None)])
        output, _reads = self.eligibility([running])
        self.assertEqual(output, "ready=false\nsuperseded=\nwaiting=true\nuncovered=false\nbuild_pending=false\n")
        # And a gate that really failed is still a refusal, not a wait.
        refused = ([self.run_record()], [self.gate_run(conclusion="failure")])
        output, _reads = self.eligibility([refused])
        self.assertEqual(output, "ready=false\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")

    def test_the_deploy_job_repairs_a_sparse_workspace_before_checking_out(self):
        # box-diag's dev-box job shares this runner and left the workspace in a
        # non-cone sparse checkout: actions/checkout then reports green with
        # deploy/ absent, and both the gate and the release script fail on a
        # missing file (2026-10-18 09:46, 10:01). Repairing after a checkout is
        # too late — the repair has to be the step before it.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        steps = workflow["jobs"]["deploy"]["steps"]
        self.assertNotIn("uses", steps[0], "the repair must not be a checkout")
        self.assertEqual(steps[0]["name"], "Repair a sparse workspace")
        checkouts = [i for i, step in enumerate(steps)
                     if step.get("uses", "").startswith("actions/checkout@")
                     or "git-fetch.sh" in step.get("run", "")]
        self.assertEqual(checkouts[:1], [1], "a checkout runs before the workspace is repaired")
        body = steps[0]["run"]
        for fragment in ("core.sparseCheckout", "git sparse-checkout disable",
                         "info/sparse-checkout", "::warning::"):
            self.assertIn(fragment, body)

    def test_drift_converges_to_the_green_anchor_and_never_builds_main(self):
        # Drift against main's tip is the wrong question now: main moving ahead
        # of the anchor is a batch still under test, and firing or building its
        # commit is the bypass this file's guard exists to stop (2026-10-18
        # 11:33). The anchor, or nothing.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-drift.yml").read_text())
        body = next(step["run"] for step in workflow["jobs"]["drift"]["steps"]
                    if "converge" in step.get("run", ""))
        self.assertIn("git/ref/heads/main-green", body)
        self.assertIn('TARGET_SHA="$anchor"', body)
        self.assertNotIn("gh workflow run build.yml", body)
        self.assertNotIn("image-tag.sh origin/main", body)
        # Everything the build-run lookup asks about has to be the anchor too:
        # asking main's tip would read "a build is in flight" and skip the hour
        # while the anchor's own images never arrive.
        self.assertIn('full_sha="$anchor"', body)
        self.assertNotIn("rev-parse origin/main", body)
        # A failed read must not read as "there is no anchor yet".
        self.assertIn("404", body)
        self.assertIn("::warning::", body)

    def test_the_bot_check_reads_the_actor_context(self):
        # The guard is only as good as the context it reads: another actor
        # expression would leave the bot dispatch looking like a person.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        for job, step_id in (("eligibility", "check"), ("deploy", "release")):
            step = next(step for step in workflow["jobs"][job]["steps"] if step.get("id") == step_id)
            self.assertEqual(step["env"]["ACTOR"], "${{ github.actor }}", job)
            self.assertEqual(step["env"]["REF_INPUT"], "${{ inputs.ref }}", job)
        self.assertIn("recovery", workflow[True]["workflow_dispatch"]["inputs"])

    def test_a_bot_dispatch_takes_the_guard_like_any_automatic_release(self):
        # The drift convergence dispatches this workflow with `-f ref=<anchor>`.
        # On 2026-10-18 that dispatch reached dev as a "manual release" and put
        # a commit whose main batch had not run on the box. The actor is the
        # part that tells a person from the bot.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        check = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                     if step.get("id") == "check")
        release = next(step for step in workflow["jobs"]["deploy"]["steps"]
                       if step.get("id") == "release")
        for step in (check, release):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                result = subprocess.run(
                    ["bash", "-eu", "-c", step["run"]],
                    cwd=directory if step is check else ROOT,
                    capture_output=True, text=True,
                    env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch",
                         "ACTOR": "github-actions[bot]", "REF_INPUT": "a" * 40,
                         "REBUILT": "", "CANDIDATE_SHA": "a" * 40,
                         "GITHUB_OUTPUT": str(output)})
                self.assertNotEqual(result.returncode, 0, "a bot dispatch took the manual bypass")
                self.assertIn("check-auto-deploy.py", result.stderr)
                self.assertFalse(output.exists() and output.read_text())

    def test_the_slot_recovery_skips_only_the_backwards_guard(self):
        # #2770: the box serves the -b slots and main's tree has no slots, so
        # the only commit that can move it back is older than what it runs. The
        # convergence names it and sets recovery=true. Required CI is still
        # required (the eligibility job does not read recovery), and only the
        # release step's backwards guard is passed over.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        check = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                     if step.get("id") == "check")
        release = next(step for step in workflow["jobs"]["deploy"]["steps"]
                       if step.get("id") == "release")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = subprocess.run(
                ["bash", "-eu", "-c", check["run"]], cwd=directory, capture_output=True, text=True,
                env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch",
                     "ACTOR": "github-actions[bot]", "REF_INPUT": "a" * 40,
                     "RECOVERY": "true", "REBUILT": "", "CANDIDATE_SHA": "a" * 40,
                     "GITHUB_OUTPUT": str(output)})
            self.assertNotEqual(result.returncode, 0, "a bot recovery skipped the CI check")
            self.assertIn("check-auto-deploy.py", result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "deploy").mkdir()
            (work / "deploy" / "check-auto-deploy.py").write_text("")
            output = work / "output"
            result = subprocess.run(
                ["bash", "-eu", "-c", release["run"]], cwd=work, capture_output=True, text=True,
                env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch",
                     "ACTOR": "github-actions[bot]", "REF_INPUT": "a" * 40,
                     "RECOVERY": "true", "REBUILT": "", "CANDIDATE_SHA": "a" * 40,
                     "GITHUB_OUTPUT": str(output)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skip=false", output.read_text())

    def test_a_recovery_dispatch_clears_superseded_so_the_job_runs(self):
        # Without this the eligibility job answers superseded=<newer main tip>,
        # and the deploy job's `superseded == ''` skips the whole release —
        # the slot recovery would be dead code (2026-10-18 review).
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        check = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                     if step.get("id") == "check")
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            stub = work / "deploy"
            stub.mkdir()
            script = stub / "check-auto-deploy.py"
            script.write_text(
                "import os\n"
                "with open(os.environ['GITHUB_OUTPUT'],'a') as f:\n"
                "    f.write('ready=true\\nsuperseded=' + 'd'*40 + '\\nwaiting=false\\nuncovered=false\\n')\n")
            output = work / "output"
            result = subprocess.run(
                ["bash", "-eu", "-c", check["run"]], cwd=work, capture_output=True, text=True,
                env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch",
                     "ACTOR": "github-actions[bot]", "REF_INPUT": "a" * 40,
                     "RECOVERY": "true", "REBUILT": "", "CANDIDATE_SHA": "a" * 40,
                     "GITHUB_OUTPUT": str(output)})
            self.assertEqual(result.returncode, 0, result.stderr)
            text = output.read_text()
            self.assertIn("ready=true", text)
            self.assertIn("superseded=\n", text, text)
            self.assertNotIn("superseded=" + "d" * 40, text)

    def test_a_bare_dispatch_is_not_a_manual_release_either(self):
        # No ref names nothing a person chose; releasing the dispatch ref's
        # HEAD that way is how a stale bot dispatch let a batch through.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        check = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                     if step.get("id") == "check")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = subprocess.run(
                ["bash", "-eu", "-c", check["run"]], cwd=directory, capture_output=True, text=True,
                env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch",
                     "ACTOR": "wangchangxin", "REF_INPUT": "", "REBUILT": "",
                     "CANDIDATE_SHA": "a" * 40, "GITHUB_OUTPUT": str(output)})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("check-auto-deploy.py", result.stderr)

    def test_the_drift_job_repairs_the_same_shared_workspace(self):
        # drift runs deploy/ scripts from the same checkout on the same runner;
        # it failed on the same missing file (09:53). Its own copy of the step
        # has to do the same work, not just carry the same name.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-drift.yml").read_text())
        steps = workflow["jobs"]["drift"]["steps"]
        self.assertEqual(steps[0]["name"], "Repair a sparse workspace")
        self.assertTrue(steps[1].get("uses", "").startswith("actions/checkout@"))
        body = steps[0]["run"]
        for fragment in ("core.sparseCheckout", "git sparse-checkout disable",
                         "info/sparse-checkout", "::warning::"):
            self.assertIn(fragment, body)

    def test_the_repair_step_actually_restores_a_sparse_checkout(self):
        # Behavioural, against a real repository: leave it in the shape
        # box-diag does, run the step's own shell, and require that the files
        # outside the cone are back and the sparse config is gone.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        body = workflow["jobs"]["deploy"]["steps"][0]["run"]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "ws"
            repo.mkdir()

            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True,
                                      capture_output=True, text=True)

            git("init", "--quiet")
            (repo / "deploy").mkdir()
            (repo / "deploy" / "keep.sh").write_text("echo keep\n")
            (repo / "outside.txt").write_text("outside\n")
            git("add", "deploy/keep.sh", "outside.txt")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            git("sparse-checkout", "set", "--no-cone", "deploy")
            self.assertTrue((repo / "deploy" / "keep.sh").exists())
            self.assertFalse((repo / "outside.txt").exists(), "fixture is not sparse")
            # Prove the fixture is really sparse by the same signal the repair
            # has to remove — the config key alone lives at a scope that the
            # fixture does not write.
            before = subprocess.run(["git", "sparse-checkout", "list"], cwd=repo,
                                    capture_output=True, text=True)
            self.assertEqual(before.returncode, 0)
            self.assertIn("deploy", before.stdout)

            env = {**os.environ, "GITHUB_WORKSPACE": str(repo)}
            proc = subprocess.run(["bash", "-e", "-c", body], env=env,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((repo / "outside.txt").exists(),
                            "the repair left the workspace sparse")
            after = subprocess.run(["git", "sparse-checkout", "list"], cwd=repo,
                                   capture_output=True, text=True)
            self.assertNotEqual(after.returncode, 0, "the workspace is still sparse")
            info = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo,
                                  capture_output=True, text=True, check=True).stdout.strip()
            self.assertFalse((repo / info / "info" / "sparse-checkout").exists())

    def test_workflow_checks_eligibility_before_reserving_deploy_runner(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        self.assertEqual(workflow["jobs"]["deploy"]["needs"], "eligibility")
        self.assertEqual(workflow["jobs"]["deploy"]["if"].strip(),
                         "needs.eligibility.outputs.ready == 'true' && needs.eligibility.outputs.superseded == ''")
        self.assertNotIn("cheese-dev", workflow["jobs"]["eligibility"]["runs-on"])
        self.assertNotIn("concurrency", workflow)
        self.assertEqual(workflow["jobs"]["deploy"]["concurrency"]["group"], "deploy-dev")
        triggers = workflow.get("on", workflow.get(True))
        # One release run per merged commit: Required CI is the event. The
        # build used to trigger one too, and it almost always arrived first and
        # refused for want of the gate -- a second run per commit whose
        # "success" read as "this commit is on dev".
        self.assertEqual(set(triggers["workflow_run"]["workflows"]), {"Required CI"})
        # Room for the initial settle plus the wait, with room to spare for the
        # API calls; 12 minutes equalled the sleep budget alone and the job
        # could be killed mid-check instead of refusing.
        self.assertGreaterEqual(
            workflow["jobs"]["eligibility"]["timeout-minutes"] * 60,
            GUARD.SETTLE_SECONDS + GUARD.BUILD_WAIT_SECONDS + 120,
        )
        self.assertNotIn("build-did-not-produce-images", workflow["jobs"])
        # The refusal has to pass "the build was still running" on, or it
        # prints "no successful attempt" minutes after the build succeeded.
        self.assertIn("build_pending", workflow["jobs"]["eligibility"]["outputs"])
        reason = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                      if step.get("name") == "Fail when this commit will not be deployed")
        self.assertEqual(reason["env"]["BUILD_PENDING"],
                         "${{ steps.check.outputs.build_pending }}")
        self.assertIn('"$BUILD_PENDING" = true', reason["run"])
        self.assertIn("still running", reason["run"])
        # The manual states the wait in minutes; keep it tied to the constant.
        manual = (ROOT / "docs/manual/dev/ci.md").read_text()
        self.assertIn(f"最多 {GUARD.BUILD_WAIT_SECONDS // 60} 分钟", manual)

    def test_required_ci_runs_on_the_main_push(self):
        # A commit reaches main as a push; that run is the gate this deployment reads.
        required = yaml.safe_load((ROOT / ".github/workflows/required-ci.yml").read_text())
        triggers = required.get("on", required.get(True))
        self.assertEqual(triggers["push"]["branches"], ["main"])

    def test_only_a_main_push_gate_run_that_could_release_reaches_eligibility(self):
        """The Required CI completion now triggers this workflow, so the job's
        own condition is what keeps a failed or unrelated gate run from turning
        into a red deploy run. A cancelled one is admitted on purpose: the
        entry point tells "a newer batch will cover it" from "nothing will",
        and the second has to reach the refusal to be said at all."""
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        condition = workflow["jobs"]["eligibility"]["if"]

        def holds(event_name, name=None, event=None, branch=None, conclusion=None):
            expression = " ".join(condition.split())
            substituted = {
                "github.event_name": repr(event_name),
                "github.event.workflow_run.name": repr(name),
                "github.event.workflow_run.event": repr(event),
                "github.event.workflow_run.head_branch": repr(branch),
                "github.event.workflow_run.conclusion": repr(conclusion),
            }
            for token, value in substituted.items():
                expression = expression.replace(token, value)
            expression = expression.replace("||", " or ").replace("&&", " and ")
            return bool(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307

        self.assertTrue(holds("workflow_run", "Required CI", "push", "main", "success"))
        self.assertTrue(holds("workflow_run", "Required CI", "push", "main", "cancelled"))
        # The build no longer triggers this workflow; if it ever does again,
        # the eligibility job refuses it rather than guessing.
        self.assertFalse(holds("workflow_run", "Build and Push Docker Image", "push", "main", "success"))
        self.assertTrue(holds("workflow_dispatch"))
        for named in (
            ("Required CI", "push", "main", "failure"),
            # A pull request whose head branch is called main is admitted by
            # `branches: [main]` too, and its commit is not one to release.
            ("Required CI", "pull_request", "main", "success"),
            # A push run on some other branch (a fork's main, a tag push) is
            # likewise not this repository's main.
            ("Required CI", "push", "someone-elses-main", "success"),
            # A merge-queue entry runs on the queue's temporary branch.
            ("Required CI", "merge_group", f"gh-readonly-queue/main/pr-1-{'b' * 40}", "success"),
        ):
            with self.subTest(named=named):
                self.assertFalse(holds("workflow_run", *named))

    def test_ci_rerun_while_waiting_for_deploy_runner_fails_the_release(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                GUARD.sys, "argv", ["check-auto-deploy.py", self.candidate]
            ), patch.object(GUARD, "ci_ready", return_value=False), patch.object(GUARD, "should_skip") as deploy:
                with self.assertRaises(SystemExit) as refused:
                    GUARD.main()
            self.assertIn(self.candidate, str(refused.exception.code))
            # A refusal is not a skip: a skip would leave the deploy job green.
            written = output.read_text() if output.exists() else ""
            self.assertNotIn("skip=", written)
            # And it says it refused, so the deploy alert does not page on it.
            self.assertIn("refused=true", written)
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
        self.assertEqual(step["if"],
                         "steps.check.outputs.ready != 'true' && steps.check.outputs.waiting != 'true'")
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

    def test_the_refusal_says_when_a_cancelled_gate_nothing_covers(self):
        # "No successful latest attempt" is true but useless for this case: the
        # run existed and was cancelled, and no later batch will cover it. The
        # summary has to name that, or the reader goes looking for a CI bug.
        step = self.step("eligibility", "Fail when this commit will not be deployed")
        # The value comes from the check step's own output, not from a test's
        # environment: read the wiring here, or a step pointed at the wrong
        # output stays green.
        self.assertEqual(step["env"]["UNCOVERED"],
                         "${{ steps.check.outputs.uncovered }}")
        # ...and the script that runs there is the one that writes it.
        self.assertIn("uncovered=",
                      (ROOT / "deploy" / "check-auto-deploy.py").read_text())
        for uncovered, expected in (("true", True), ("false", False)):
            with self.subTest(uncovered=uncovered), tempfile.TemporaryDirectory() as directory:
                summary = Path(directory) / "summary"
                result = subprocess.run(
                    ["bash", "-e", "-c", step["run"]], capture_output=True, text=True,
                    env={**os.environ, "PATH": self.fake_bin(directory, curl="exit 7"),
                         "CANDIDATE_SHA": self.candidate, "UNCOVERED": uncovered,
                         "GITHUB_STEP_SUMMARY": str(summary)})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual("no newer commit on main" in summary.read_text(), expected)
                if expected:
                    self.assertIn("cancelled", summary.read_text())

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
                record("required-ci.yml", ci, "push", "main")]

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
        self.assertEqual(output, f"ready=true\nsuperseded={self.newer}\nwaiting=false\nuncovered=false\nbuild_pending=false\n")
        self.assertIn(f"Not deploying {self.candidate}: {self.newer}, newer on main", printed)

    def test_the_newest_commit_that_is_ready_is_the_one_named(self):
        output, _ = self.eligibility([self.newer, self.newest], {
            self.candidate: self.runs(self.candidate), self.newer: self.runs(self.newer),
            self.newest: self.runs(self.newest, build=None)})
        self.assertEqual(output, f"ready=true\nsuperseded={self.newer}\nwaiting=false\nuncovered=false\nbuild_pending=false\n")

    def test_a_newer_commit_that_cannot_be_released_yet_does_not_supersede(self):
        for build, ci in ((None, "success"), ("failure", "success"), ("success", "failure")):
            with self.subTest(build=build, ci=ci):
                output, printed = self.eligibility([self.newer], {
                    self.candidate: self.runs(self.candidate), self.newer: self.runs(self.newer, build=build, ci=ci)})
                self.assertEqual(output, "ready=true\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")
                # A newer commit's state is not this release's refusal.
                self.assertNotIn(f"Not deploying {self.newer}", printed)

    def test_main_newest_commit_deploys(self):
        output, _ = self.eligibility([], {self.candidate: self.runs(self.candidate)})
        self.assertEqual(output, "ready=true\nsuperseded=\nwaiting=false\nuncovered=false\nbuild_pending=false\n")

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
                    expressions.add(step["env"]["REF"])
                    self.assertIn('"$REF"', step["run"], "the built commit's checkout fetches a ref other than REF")
        self.assertEqual(expressions, {"${{ inputs.rebuilt || inputs.ref || github.event.workflow_run.head_sha || github.sha }}"})
        inputs = workflow[True]["workflow_dispatch"]["inputs"]
        self.assertIn("rebuilt", inputs)
        # The rollback entry: dispatch on main, name the commit in `ref`. Every
        # expression above has to read it, or the run would release one commit
        # and say another.
        self.assertIn("ref", inputs)
        self.assertEqual(inputs["ref"]["default"], "")

    def test_a_ref_dispatch_by_a_person_is_a_manual_release(self):
        # A bare commit SHA is not a legal `--ref` for `gh workflow run`, so
        # releasing one without inventing a ref is what the `ref` input is for.
        # It takes the manual bypass — that is what lets an operator roll the
        # box back to a commit the automatic "no going backwards" guard refuses.
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        check = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                     if step.get("id") == "check")
        release = next(step for step in workflow["jobs"]["deploy"]["steps"]
                       if step.get("id") == "release")
        for step in (check, release):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                result = subprocess.run(
                    ["bash", "-eu", "-c", step["run"]],
                    cwd=directory if step is check else ROOT,
                    capture_output=True, text=True,
                    env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch",
                         "REBUILT": "", "REF_INPUT": "main", "ACTOR": "wangchangxin",
                         "CANDIDATE_SHA": self.built, "GITHUB_OUTPUT": str(output)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(("ready=true" if step is check else "skip=false"), output.read_text())

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

class MeteringReleasePolicy(unittest.TestCase):
    def run_release(self, event, rebuilt="", ci_status=0, release_status=0):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        step = next(step for step in workflow["jobs"]["deploy"]["steps"]
                    if step.get("name") == "Release the verified metering image")
        shell = shutil.which("bash")
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for command, status in (("python3", "CI_STATUS"), ("bash", "RELEASE_STATUS")):
                executable = work / command
                executable.write_text(
                    f'#!/bin/sh\nprintf \'%s\\n\' "{command} $*" >> "$CALLS"\nexit "${status}"\n')
                executable.chmod(0o755)
            env = {**os.environ, "GITHUB_EVENT_NAME": event,
                   "PATH": f"{work}:{os.environ['PATH']}", "CALLS": str(work / "calls"),
                   "CI_STATUS": str(ci_status), "RELEASE_STATUS": str(release_status)}
            env.pop("REBUILT", None)
            env.update({key: str(value).replace("${{ inputs.rebuilt }}", rebuilt)
                        for key, value in step.get("env", {}).items()})
            result = subprocess.run(
                [shell, "-eu", "-c", step["run"]], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=10)
            calls = (work / "calls").read_text().splitlines()
        return result, calls

    def expected_calls(self):
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        return [f"python3 deploy/check-auto-deploy.py --require-ci {sha}",
                f"bash deploy/release-metering-proxy.sh {sha}"]

    def test_manual_release_uses_the_existing_manual_ci_policy(self):
        result, calls = self.run_release("workflow_dispatch", ci_status=17)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, self.expected_calls()[1:])

    def test_automatic_release_requires_ci_before_metering(self):
        for status in (0, 17):
            with self.subTest(ci_status=status):
                result, calls = self.run_release("workflow_run", ci_status=status)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(calls, self.expected_calls() if status == 0 else self.expected_calls()[:1])

    def test_desktop_rebuild_requires_ci_before_metering(self):
        for status in (0, 17):
            with self.subTest(ci_status=status):
                result, calls = self.run_release("workflow_dispatch", rebuilt="b" * 40, ci_status=status)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(calls, self.expected_calls() if status == 0 else self.expected_calls()[:1])

    def test_manual_release_preserves_metering_failure(self):
        result, calls = self.run_release("workflow_dispatch", ci_status=17, release_status=23)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(calls, self.expected_calls()[1:])


if __name__ == "__main__":
    unittest.main()
