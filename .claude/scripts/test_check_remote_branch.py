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

The base cases pin the opposite failure too: a refresh that reports success
without moving the cache. A bare-name `git fetch origin main` resolves to a
tag named main, and in a checkout that never tracked main it writes only
FETCH_HEAD; both exit 0 while `refs/remotes/origin/main` stays put, which
would leave the report stale forever.

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
FETCH_COMMAND = "git fetch origin +refs/heads/main:refs/remotes/origin/main"

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

    def run_script(
        self,
        cwd: Path,
        *args: str,
        script: Path = SCRIPT,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def check(self, *args: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        return self.run_script(self.clone, *args, script=script)

    def make_remote(
        self,
        name: str,
        *,
        default_branch: str = "main",
        branches: list[str] = (),
        tag_main: bool = False,
    ) -> tuple[Path, Path, Path]:
        """A bare remote with ``branches``, cloned with its default branch only."""
        remote = self.root / f"{name}.git"
        seed = self.root / f"{name}-seed"
        clone = self.root / f"{name}-clone"
        self.git(self.root, "init", "-q", "--bare", str(remote))
        self.git(self.root, "init", "-q", "-b", default_branch, str(seed))
        self.git(seed, *self._identity, "commit", "-q", "--allow-empty", "-m", "one")
        for branch in branches:
            self.git(seed, "branch", branch)
        if tag_main:
            self.git(seed, "tag", "main")
        self.git(seed, "remote", "add", "origin", str(remote))
        refspecs = [f"refs/heads/{default_branch}:refs/heads/{default_branch}"]
        refspecs += [f"refs/heads/{b}:refs/heads/{b}" for b in branches]
        if tag_main:
            refspecs.append("refs/tags/main:refs/tags/main")
        self.git(seed, "push", "-q", "origin", *refspecs)
        self.git(
            self.root,
            "clone",
            "-q",
            "--single-branch",
            "--branch",
            default_branch,
            str(remote),
            str(clone),
        )
        return remote, seed, clone

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
        self.assertEqual(
            self.git(self.seed, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip(),
            self.fields(result.stdout).get("REMOTE_SHA"),
            result.stdout,
        )

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
        self.assertEqual(
            FETCH_COMMAND, self.fields(stale.stdout).get("NEXT"), stale.stdout
        )
        # The branch question is still answered while the base is stale.
        self.assertEqual("yes", self.fields(stale.stdout).get("EXISTS"), stale.stdout)

        refreshed = self.check("--fetch", BRANCH)
        self.assertEqual(0, refreshed.returncode, refreshed.stdout + refreshed.stderr)
        self.assertEqual("fresh", self.fields(refreshed.stdout).get("ORIGIN_MAIN"), refreshed.stdout)
        # The cache itself has to move. A report that says "fresh" over an
        # unchanged ref is the failure this case exists for.
        self.assertEqual(
            self.git(self.seed, "rev-parse", "refs/heads/main").stdout.strip(),
            self.git(self.clone, "rev-parse", "refs/remotes/origin/main").stdout.strip(),
        )

    def test_a_missing_cache_is_stale_not_current(self) -> None:
        self.git(self.clone, "update-ref", "-d", "refs/remotes/origin/main")
        result = self.check(BRANCH)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        fields = self.fields(result.stdout)
        self.assertEqual("stale", fields.get("ORIGIN_MAIN"), result.stdout)
        self.assertEqual("-", fields.get("LOCAL_ORIGIN_MAIN"), result.stdout)
        self.assertEqual(FETCH_COMMAND, fields.get("NEXT"), result.stdout)

    def test_a_remote_without_main_is_absent_not_fresh(self) -> None:
        _, _, clone = self.make_remote("trunk", default_branch="trunk", branches=["feature/on-trunk"])
        result = self.run_script(clone, "feature/on-trunk")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        fields = self.fields(result.stdout)
        self.assertEqual("absent", fields.get("ORIGIN_MAIN"), result.stdout)
        self.assertIn("ls-remote --symref", fields.get("NEXT", ""), result.stdout)

    def test_a_tag_named_main_does_not_swallow_the_fetch(self) -> None:
        # `git fetch origin main` resolves the bare name to the tag: exit 0,
        # FETCH_HEAD only, and the cache never moves. The fetch has to be
        # spelled with its refspec for this case to converge.
        _, seed, clone = self.make_remote("tagged", tag_main=True)
        self.git(seed, *self._identity, "commit", "-q", "--allow-empty", "-m", "two")
        self.git(seed, "push", "-q", "origin", "refs/heads/main:refs/heads/main")

        result = self.run_script(clone, "--fetch", "main")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("fresh", self.fields(result.stdout).get("ORIGIN_MAIN"), result.stdout)
        self.assertEqual(
            self.git(seed, "rev-parse", "refs/heads/main").stdout.strip(),
            self.git(clone, "rev-parse", "refs/remotes/origin/main").stdout.strip(),
        )

    def test_a_checkout_that_does_not_track_main_converges(self) -> None:
        # The repository creates this shape itself: a single-branch clone of
        # a scratch branch, which has no refs/remotes/origin/main at all.
        _, seed, clone = self.make_remote("narrow", default_branch="topic", branches=["main"])
        absent = self.git(clone, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main", check=False)
        self.assertNotEqual(0, absent.returncode, "the fixture must not track main")

        result = self.run_script(clone, "--fetch", "topic")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("fresh", self.fields(result.stdout).get("ORIGIN_MAIN"), result.stdout)
        self.assertEqual(
            self.git(seed, "rev-parse", "refs/heads/main").stdout.strip(),
            self.git(clone, "rev-parse", "refs/remotes/origin/main").stdout.strip(),
        )

    def test_an_absent_branch_names_its_push_even_with_a_stale_base(self) -> None:
        self.commit("two")
        self.git(self.seed, "push", "-q", "origin", "main")
        result = self.check("feature/never-pushed")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        fields = self.fields(result.stdout)
        self.assertIn("git push", fields.get("NEXT", ""), result.stdout)
        self.assertEqual("stale", fields.get("ORIGIN_MAIN"), result.stdout)

    def test_a_failed_fetch_does_not_make_an_absent_branch_read_as_existing(self) -> None:
        self.commit("two")
        self.git(self.seed, "push", "-q", "origin", "main")
        lock = self.clone / ".git" / "refs" / "remotes" / "origin" / "main.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("held", encoding="utf-8")
        try:
            result = self.check("--fetch", "feature/never-pushed")
        finally:
            lock.unlink()
        # Exit 3 means "the branch exists"; an absent branch must keep exit 1.
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual("no", self.fields(result.stdout).get("EXISTS"), result.stdout)

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
