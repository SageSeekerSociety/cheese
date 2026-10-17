"""The queue's scope covers every unlanded entry, not just its last hop."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("required-ci.py")


class QueueBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        # Fixture commits must not inherit the machine's signing, hooks or refs.
        self.env = {key: val for key, val in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Queue fixture",
            GIT_AUTHOR_EMAIL="queue@example.invalid",
            GIT_COMMITTER_NAME="Queue fixture",
            GIT_COMMITTER_EMAIL="queue@example.invalid",
        )
        self.git("init", "-b", "main")
        self.landed = self.commit("README.md")
        self.git("checkout", "-b", "gh-readonly-queue/main/prefix")
        self.prefix = self.commit("backend/app/queued.py")
        self.head = self.commit(".claude/scripts/narrow.py")
        self.git("remote", "add", "origin", str(self.repo))

    def git(self, *args, cwd=None):
        return subprocess.check_output(
            ["git", *args], cwd=cwd or self.repo, env=self.env, stderr=subprocess.PIPE
        ).decode().strip()

    def commit(self, name):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n")
        self.git("add", name)
        self.git("commit", "-m", "fixture change")
        return self.git("rev-parse", "HEAD")

    def scope(self, event="merge_group", cwd=None):
        output = self.root / "output"
        output.write_text("")
        env = dict(self.env, GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(output))
        # No ambient event file or main-ref override may alter the fixture.
        env.pop("GITHUB_EVENT_PATH", None)
        env.pop("MERGE_GROUP_MAIN_REF", None)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "scope", self.prefix],
            cwd=cwd or self.repo,
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        return result, dict(line.split("=", 1) for line in output.read_text().splitlines())

    def assert_cumulative(self, outputs):
        self.assertEqual(outputs["backend"], "true")
        self.assertEqual(outputs["guards"], "true")
        # Selecting everything would hide a broken baseline just as well.
        self.assertEqual(outputs["frontend"], "false")
        self.assertEqual(outputs["tier"], "full")

    def test_queue_includes_backend_prefix_before_guard_only_successor(self):
        result, outputs = self.scope()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_cumulative(outputs)
        self.assertIn(f"Event base: {self.prefix}", result.stdout)
        self.assertIn(f"Effective base: {self.landed}", result.stdout)
        self.assertIn("unlanded queue prefix", result.stdout)
        self.assertIn("whose changes remain in coverage", result.stdout)

    def test_receipt_base_is_the_base_used_by_the_suite_diff(self):
        result, outputs = self.scope()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_base = next(
            line.partition(": ")[2]
            for line in result.stdout.splitlines()
            if line.startswith("Effective base: ")
        )
        changed = set(
            self.git("diff", "--name-only", "--no-renames", receipt_base, self.head).splitlines()
        )
        self.assertEqual(
            changed,
            {"backend/app/queued.py", ".claude/scripts/narrow.py"},
        )
        self.assertEqual(outputs["backend"], "true")
        self.assertEqual(outputs["frontend"], "false")

    def test_non_queue_keeps_the_supplied_base(self):
        result, outputs = self.scope(event="pull_request")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {suite for suite, value in outputs.items() if value == "true"}, {"guards"}
        )

    def test_landed_prefix_no_longer_needs_backend(self):
        self.git("update-ref", "refs/heads/main", self.prefix)
        result, outputs = self.scope()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["backend"], "false")
        self.assertEqual(outputs["guards"], "true")

    def test_main_advanced_on_another_branch_uses_common_ancestor(self):
        self.git("checkout", "main")
        self.commit("frontend/landed.ts")
        self.git("checkout", self.head)
        result, outputs = self.scope()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_cumulative(outputs)

    def test_depth_one_checkout_and_base_fetch_still_cover_prefix(self):
        checkout = self.root / "checkout"
        self.git(
            "clone", "--depth=1", "--no-local", "--branch",
            "gh-readonly-queue/main/prefix", str(self.repo), str(checkout),
        )
        self.git("fetch", "--depth=1", "origin", self.prefix, cwd=checkout)
        self.assertEqual(self.git("rev-parse", "--is-shallow-repository", cwd=checkout), "true")
        # actions/checkout's single-branch clone has no origin/main yet.
        self.assertNotEqual(
            subprocess.run(
                ["git", "rev-parse", "--verify", "refs/remotes/origin/main"],
                cwd=checkout, env=self.env, check=False, capture_output=True,
            ).returncode,
            0,
        )
        result, outputs = self.scope(cwd=checkout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_cumulative(outputs)

    def test_unavailable_main_fails_without_publishing_partial_scope(self):
        self.git("update-ref", "-d", "refs/heads/main")
        result, outputs = self.scope()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outputs, {})

    def test_unrelated_main_fails_without_falling_back_to_last_hop(self):
        self.git("checkout", "--orphan", "unrelated")
        self.git("rm", "-rf", ".")
        unrelated = self.commit("unrelated.txt")
        self.git("update-ref", "refs/heads/main", unrelated)
        self.git("checkout", self.head)
        result, outputs = self.scope()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outputs, {})

    def check(self, **github_values):
        env = dict(self.env)
        for key in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
            env.pop(key, None)
        env.update(
            GITHUB_EVENT_NAME="merge_group",
            CI_NEEDS=json.dumps({"scope": {"result": "success", "outputs": {"tier": "full"}}}),
            **github_values,
        )
        return subprocess.run(
            [sys.executable, str(SCRIPT), "check"],
            cwd=self.repo,
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_check_failure_without_run_environment_says_no_link(self):
        for values in (
            {},
            {"GITHUB_SERVER_URL": "https://github.example.test", "GITHUB_RUN_ID": ""},
            {"GITHUB_SERVER_URL": "", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "12345"},
            {"GITHUB_SERVER_URL": "https://github.example.test", "GITHUB_REPOSITORY": "", "GITHUB_RUN_ID": "12345"},
        ):
            with self.subTest(values=values):
                result = self.check(**values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("无链接", result.stderr)
                self.assertNotIn("runs/None", result.stderr)

    def test_each_check_failure_includes_the_actions_run_url(self):
        result = self.check(
            GITHUB_SERVER_URL="https://github.example.test/",
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_RUN_ID="12345",
        )
        self.assertNotEqual(result.returncode, 0)
        lines = result.stderr.splitlines()
        self.assertTrue(lines)
        self.assertTrue(
            all("https://github.example.test/owner/repo/actions/runs/12345" in line for line in lines),
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
