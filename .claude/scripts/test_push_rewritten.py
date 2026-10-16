#!/usr/bin/env python3
"""push-rewritten.sh against throwaway git repositories.

    python3 .claude/scripts/test_push_rewritten.py

Each case builds its own bare "remote" and one or two checkouts under a temp
directory, with the ambient git config switched off, so no network, no
credentials, and no host setting (commit.gpgsign, a global default branch) can
turn a pass into a false result.

The cases are the rules the script exists to keep, stated before it was written:

  1. a task/* branch amended after it was pushed is force-pushed into the
     remote, and the remote ends up at the amended commit;
  2. the same run on `main` is refused and the remote is untouched;
  3. a checkout that already sees a remote commit it does not have is refused,
     because forcing there would drop that commit;
  4. the remote advanced past what the checkout last fetched, so the lease is
     rejected, nothing is pushed, and the message points at `git fetch`;
  5. a plain fast-forward is refused, because the normal push does it;
  6. a fetched remote commit nobody integrated is refused — the fetch alone must
     not turn "drop their commit" into "force it away";
  7. another history pushed under the same branch name is refused; both sides
     look diverged to a commit count, and only the reflog tells them apart.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".claude" / "scripts" / "push-rewritten.sh"

ENV = dict(os.environ)
# A caller that is itself inside a git command (a hook, `git -c ...`) exports
# these; inherited, they would point every fixture command at the caller's repo
# instead of the throwaway one, and the suite would test the wrong tree.
for _leak in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    ENV.pop(_leak, None)
ENV.update(
    GIT_CONFIG_NOSYSTEM="1",
    GIT_CONFIG_GLOBAL=os.devnull,
    GIT_AUTHOR_NAME="push-rewritten test",
    GIT_AUTHOR_EMAIL="push-rewritten@example.invalid",
    GIT_COMMITTER_NAME="push-rewritten test",
    GIT_COMMITTER_EMAIL="push-rewritten@example.invalid",
    GIT_AUTHOR_DATE="2020-01-01T00:00:00+00:00",
    GIT_COMMITTER_DATE="2020-01-01T00:00:00+00:00",
)


def git(cwd, *args, check=True):
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd),
        env=ENV,
        check=check,
        capture_output=True,
        text=True,
    )


def write(path, text):
    path.write_text(text)


class Fixture:
    """A bare remote plus a checkout, built with the ambient config off."""

    def __init__(self, root):
        self.root = Path(root)
        self.upstream = self.root / "upstream.git"
        git(self.root, "init", "--bare", "-q", "-b", "main", str(self.upstream))
        self.work = self.root / "work"
        git(self.root, "clone", "-q", str(self.upstream), str(self.work))
        write(self.work / "README.md", "seed\n")
        git(self.work, "add", "README.md")
        git(self.work, "commit", "-q", "-m", "seed")
        git(self.work, "push", "-q", "-u", "origin", "main")

    def clone(self, name):
        """A second checkout of the same remote."""
        other = self.root / name
        git(self.root, "clone", "-q", str(self.upstream), str(other))
        return other

    def head(self, cwd=None):
        return git(cwd or self.work, "rev-parse", "HEAD").stdout.strip()

    def remote_ref(self, ref="refs/heads/task/42"):
        return git(self.upstream, "rev-parse", ref).stdout.strip()

    def commit(self, cwd, filename, text, message):
        write(Path(cwd) / filename, text)
        git(cwd, "add", filename)
        git(cwd, "commit", "-q", "-m", message)


class PushRewrittenTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="push-rewritten-")
        self.addCleanup(tmp.cleanup)
        self.fx = Fixture(tmp.name)

    def run_script(self, cwd, *args):
        return subprocess.run(
            [str(SCRIPT), *args],
            cwd=str(cwd),
            env=ENV,
            capture_output=True,
            text=True,
        )

    def pushed_task_branch(self):
        """A task/42 branch pushed to the remote; returns the pushed commit."""
        fx = self.fx
        git(fx.work, "checkout", "-q", "-b", "task/42")
        fx.commit(fx.work, "file.txt", "first\n", "first")
        git(fx.work, "push", "-q", "-u", "origin", "task/42")
        return fx.head()

    def theirs_on_the_remote(self):
        """Another checkout advances task/42; returns the commit it pushed."""
        fx = self.fx
        other = fx.clone("other")
        git(other, "checkout", "-q", "task/42")
        fx.commit(other, "theirs.txt", "theirs\n", "their commit")
        git(other, "push", "-q", "origin", "task/42")
        return fx.remote_ref()

    def test_amended_task_branch_is_force_pushed(self):
        fx = self.fx
        pushed = self.pushed_task_branch()

        # A message-only amend: the tree is byte-identical to the pushed commit,
        # which is the case `cheese push-fix` refuses on commit id alone.
        git(fx.work, "commit", "-q", "--amend", "-m", "first, reworded")
        amended = fx.head()
        self.assertNotEqual(amended, pushed, "the amend did not rewrite the commit")

        result = self.run_script(fx.work)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fx.remote_ref(), amended)

    def test_main_is_refused_and_untouched(self):
        fx = self.fx
        before = git(fx.upstream, "rev-parse", "refs/heads/main").stdout.strip()

        result = self.run_script(fx.work)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("task/", result.stderr)
        self.assertEqual(
            git(fx.upstream, "rev-parse", "refs/heads/main").stdout.strip(), before
        )

    def test_checkout_behind_the_remote_is_refused(self):
        fx = self.fx
        self.pushed_task_branch()
        theirs = self.theirs_on_the_remote()

        git(fx.work, "fetch", "-q", "origin")

        result = self.run_script(fx.work)

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(fx.remote_ref(), theirs)

    def test_remote_ahead_of_last_fetch_is_refused(self):
        fx = self.fx
        self.pushed_task_branch()
        theirs = self.theirs_on_the_remote()

        git(fx.work, "commit", "-q", "--amend", "-m", "mine, reworded")
        self.assertNotEqual(fx.head(), theirs)

        result = self.run_script(fx.work)

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(fx.remote_ref(), theirs)
        self.assertIn("fetch", result.stderr)

    def test_fast_forward_is_refused(self):
        fx = self.fx
        self.pushed_task_branch()
        before = fx.remote_ref()
        fx.commit(fx.work, "more.txt", "more\n", "more")

        result = self.run_script(fx.work)

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(fx.remote_ref(), before)

    def test_fetched_but_never_integrated_remote_commit_is_refused(self):
        """Fetching is not integrating: a fetch must not license dropping it."""
        fx = self.fx
        self.pushed_task_branch()
        theirs = self.theirs_on_the_remote()

        git(fx.work, "commit", "-q", "--amend", "-m", "mine, reworded")
        git(fx.work, "fetch", "-q", "origin")

        result = self.run_script(fx.work)

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(fx.remote_ref(), theirs)
        self.assertIn("fetch", result.stderr)

    def test_unrelated_history_under_the_branch_name_is_refused(self):
        """Both counts are non-zero here too; only the reflog rules it out."""
        fx = self.fx
        self.pushed_task_branch()

        other = fx.clone("other")
        git(other, "checkout", "-q", "--orphan", "task/42")
        fx.commit(other, "theirs.txt", "theirs\n", "their root")
        git(other, "push", "-q", "--force", "origin", "task/42")
        theirs = fx.remote_ref()

        git(fx.work, "fetch", "-q", "origin")
        counts = git(
            fx.work,
            "rev-list",
            "--left-right",
            "--count",
            "HEAD...refs/remotes/origin/task/42",
        ).stdout.split()
        self.assertEqual(len(counts), 2, counts)
        self.assertTrue(all(int(count) > 0 for count in counts), counts)

        result = self.run_script(fx.work)

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(fx.remote_ref(), theirs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
