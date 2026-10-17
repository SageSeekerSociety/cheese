#!/usr/bin/env python3
"""Prove check-remote-branch.sh answers from the remote, not from the cache.

The rule this guards is one sentence, and it is only false in one direction:
`origin/<branch>` is a cache the checkout's refspec decides, not a fact about
the remote. A task worktree cloned with `--single-branch` has a remote-tracking
ref for main and nothing else, so an implementation built on
`git rev-parse --verify origin/<branch>` reports a branch that exists — and
that a colleague pushed after the clone — as missing. The failure is silent:
the wrong answer looks exactly like the right one.

So every case here builds a real bare remote and a real single-branch clone,
and the repository state, not a mock, decides the answer. The fixture is
checked before it is trusted: a case that passed only because the clone had
the ref after all would prove nothing.

The mutant case is the one that makes the rest worth reading. It swaps in a
script that answers from `refs/remotes/origin/<branch>` and runs the very
assertion the passing case uses; that assertion has to fail. Without it, a
regression that replaced `git ls-remote` with the cached lookup would leave
the suite green, which is the whole class of bug this file exists for.

Run: python3 .claude/scripts/test_check_remote_branch.py
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check-remote-branch.sh")
BRANCH = "feature/only-on-remote"

# The implementation this guard replaced. It answers the existence question
# from the checkout's cache, so it says "no" for a branch the remote has.
LOCAL_REFS_ONLY = """\
#!/usr/bin/env bash
set -euo pipefail
branch="${1#origin/}"
if git rev-parse --verify --quiet "refs/remotes/origin/$branch" >/dev/null; then
  echo "EXISTS: yes"
  exit 0
fi
echo "EXISTS: no"
exit 1
"""


class CheckRemoteBranchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="check-remote-branch-")
        self.root = Path(self._tmp.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.clone = self.root / "clone"
        self._identity = [
            "-c",
            "user.name=check-remote-branch",
            "-c",
            "user.email=check-remote-branch@example.invalid",
        ]
        self._fixture()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- the fixture -------------------------------------------------------

    def git(
        self, cwd: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=check, capture_output=True, text=True
        )

    def commit(self, message: str) -> None:
        self.git(self.seed, *self._identity, "commit", "-q", "--allow-empty", "-m", message)

    def _fixture(self) -> None:
        """A bare remote with two branches, cloned with main only."""
        self.git(self.root, "init", "-q", "--bare", str(self.remote))
        self.git(self.root, "init", "-q", "-b", "main", str(self.seed))
        self.commit("one")
        self.git(self.seed, "branch", BRANCH)
        self.git(self.seed, "remote", "add", "origin", str(self.remote))
        self.git(self.seed, "push", "-q", "origin", "main", BRANCH)
        self.git(
            self.root,
            "clone",
            "-q",
            "--single-branch",
            "--branch",
            "main",
            str(self.remote),
            str(self.clone),
        )

    def check(self, *args: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=self.clone,
            capture_output=True,
            text=True,
        )

    def fields(self, stdout: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for line in stdout.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                found[key] = value
        return found

    # --- what the guard is for --------------------------------------------

    def test_a_branch_the_checkout_never_fetched_is_reported_as_existing(self) -> None:
        # The premise, asserted rather than assumed: this checkout really does
        # lack the ref, so a cache-based answer really would say "missing".
        cached = self.git(
            self.clone,
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/remotes/origin/{BRANCH}",
            check=False,
        )
        self.assertNotEqual(0, cached.returncode, "the fixture must not have the branch locally")

        result = self.check(BRANCH)
        self.assert_branch_exists(result)

    def assert_branch_exists(self, result: subprocess.CompletedProcess[str]) -> None:
        """The assertion both the real script and the mutant are held to."""
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("yes", self.fields(result.stdout).get("EXISTS"), result.stdout)
        self.assertNotEqual("-", self.fields(result.stdout).get("REMOTE_SHA"), result.stdout)

    def test_the_same_assertion_rejects_a_local_refs_only_implementation(self) -> None:
        mutant = self.root / "local-refs-only.sh"
        mutant.write_text(LOCAL_REFS_ONLY, encoding="utf-8")

        degraded = self.check(BRANCH, script=mutant)
        self.assertEqual("no", self.fields(degraded.stdout).get("EXISTS"), degraded.stdout)

        # The proof that this file would notice the regression: the passing
        # case's own assertion goes red on the implementation it replaced.
        with self.assertRaises(AssertionError):
            self.assert_branch_exists(degraded)

    def test_a_branch_missing_from_the_remote_is_reported_as_missing(self) -> None:
        result = self.check("feature/never-pushed")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        fields = self.fields(result.stdout)
        self.assertEqual("no", fields.get("EXISTS"), result.stdout)
        self.assertIn("git push", fields.get("NEXT", ""), result.stdout)

    # --- the two ways "cannot judge" is not "no" ---------------------------

    def test_an_unreadable_remote_is_cannot_judge_not_a_missing_branch(self) -> None:
        result = self.check("--remote", "no-such-remote", "main")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("unknown", self.fields(result.stdout).get("EXISTS"), result.stdout)

    def test_a_remote_that_cannot_be_reached_is_cannot_judge(self) -> None:
        self.git(self.clone, "remote", "set-url", "origin", str(self.root / "not-a-repo"))
        result = self.check("main")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("unknown", self.fields(result.stdout).get("EXISTS"), result.stdout)

    # --- the base the workspace compares against ---------------------------

    def test_a_stale_origin_main_names_its_fix_and_fetch_clears_it(self) -> None:
        self.commit("two")
        self.git(self.seed, "push", "-q", "origin", "main")

        stale = self.check(BRANCH)
        self.assertEqual(3, stale.returncode, stale.stdout + stale.stderr)
        self.assertEqual("stale", self.fields(stale.stdout).get("ORIGIN_MAIN"), stale.stdout)
        self.assertIn("git fetch origin main", self.fields(stale.stdout).get("NEXT", ""), stale.stdout)
        # The branch question is still answered while the base is stale.
        self.assertEqual("yes", self.fields(stale.stdout).get("EXISTS"), stale.stdout)

        refreshed = self.check("--fetch", BRANCH)
        self.assertEqual(0, refreshed.returncode, refreshed.stdout + refreshed.stderr)
        self.assertEqual("fresh", self.fields(refreshed.stdout).get("ORIGIN_MAIN"), refreshed.stdout)

    # --- the script's own self-test ---------------------------------------

    def test_the_scripts_self_test_passes(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--self-test"],
            cwd=self.clone,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS: check-remote-branch self-test", result.stdout)


if __name__ == "__main__":
    unittest.main()
