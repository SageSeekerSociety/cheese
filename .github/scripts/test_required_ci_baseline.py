"""The scope CLI diffs a pull request from the base it names; check failures link the run."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("required-ci.py")


class PullRequestBaseTests(unittest.TestCase):
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
            GIT_AUTHOR_NAME="Fixture",
            GIT_AUTHOR_EMAIL="fixture@example.invalid",
            GIT_COMMITTER_NAME="Fixture",
            GIT_COMMITTER_EMAIL="fixture@example.invalid",
        )
        self.git("init", "-b", "main")
        self.commit("README.md")
        self.git("checkout", "-b", "change")
        # A backend change the named base already contains, then a change only
        # the guards read: the diff from the named base holds the second alone.
        self.base = self.commit("backend/app/queued.py")
        self.commit(".claude/scripts/narrow.py")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, env=self.env, stderr=subprocess.PIPE
        ).decode().strip()

    def commit(self, name):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n")
        self.git("add", name)
        self.git("commit", "-m", "fixture change")
        return self.git("rev-parse", "HEAD")

    def scope(self, draft):
        output = self.root / "output"
        output.write_text("")
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {"draft": draft}}))
        env = dict(self.env, GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event),
                   GITHUB_OUTPUT=str(output))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "scope", self.base],
            cwd=self.repo, check=False, env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_a_ready_pull_request_diffs_from_the_base_it_names(self):
        outputs = self.scope(draft=False)
        self.assertEqual(outputs["tier"], "pr")
        self.assertEqual(outputs["base"], self.base)
        self.assertEqual(
            {suite for suite, value in outputs.items() if value == "true"}, {"guards"}
        )

    def test_a_draft_pull_request_selects_nothing(self):
        outputs = self.scope(draft=True)
        self.assertEqual(outputs["tier"], "draft")
        self.assertNotIn("true", outputs.values())

    def check(self, **github_values):
        env = dict(self.env)
        for key in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_EVENT_PATH"):
            env.pop(key, None)
        env.update(
            GITHUB_EVENT_NAME="push",
            CI_NEEDS=json.dumps({"scope": {"result": "success", "outputs": {"tier": "full"}}}),
            **github_values,
        )
        return subprocess.run(
            [sys.executable, str(SCRIPT), "check"],
            cwd=self.repo, check=False, env=env, capture_output=True, text=True,
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
