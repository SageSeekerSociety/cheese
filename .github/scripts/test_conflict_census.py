"""`conflict-census.py` against throwaway repositories, driven from the CLI.

Black box: the script is run as a subprocess and judged by what it prints and
what it exits with, never by importing it. Each test builds the one repository
shape its rule needs, so a fixture that stops exercising its trap fails the
test that names the trap rather than quietly passing.

Collected by `repo-guards.yml`'s "CI helper tests" step, which globs
`.github/scripts/test_*.py` — nothing has to be registered for this to run.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".claude/scripts/conflict-census.py"

BASE_FILES = {
    "backend/app/shared.py": "one\ntwo\nthree\n",
    "backend/app/x.py": "x-base\n",
    "backend/app/y.py": "y-base\n",
    "frontend/src/a11y-baseline.json": '{"v": "base"}\n',
}

#: branch -> files it changes. Every branch starts at the base commit, so what
#: decides a conflict is only whether main later changed the same text.
BRANCHES = {
    # Touches a file main never touches.
    "clean": {"backend/app/added.py": "new\n"},
    # A name with a `/` in it, the shape every PR branch in this repository has
    # (`task/<id>`). A one-level `refs/heads/*` glob does not reach it.
    "feature/nested": {"backend/app/nested.py": "new\n"},
    # Two branches change main's line the same way, differently: both conflict,
    # and on the same path, which is what the per-file count has to see.
    "conflict-a": {"backend/app/shared.py": "one-A\ntwo\nthree\n"},
    "conflict-b": {"backend/app/shared.py": "one-B\ntwo\nthree\n"},
    # `shared.py` in a region main does not touch: git merges that file by
    # itself and prints `Auto-merging backend/app/shared.py`, while `x.py`
    # below really does conflict. Reading the report by filename counts the
    # first as a conflict; it is not one.
    "mixed": {
        "backend/app/shared.py": "one\ntwo\nthree-A\n",
        "backend/app/x.py": "x-branch\n",
    },
    # The name `[a-z-]+-baseline` cannot match.
    "digits": {"frontend/src/a11y-baseline.json": '{"v": "branch"}\n'},
    # The second branch on that same path, alphabetically the last of the
    # contended files and the one two branches contend on: a list sorted by
    # path prints the count-1 files first, under a header that promises the
    # opposite order, and `--top` then trims away this one.
    "digits-two": {"frontend/src/a11y-baseline.json": '{"v": "other"}\n'},
    # Three conflicted files in one branch, so the report cannot be a
    # one-file-per-branch assumption.
    "pair": {
        "backend/app/x.py": "x-branch\n",
        "backend/app/y.py": "y-branch\n",
    },
    # A legal branch name ending in `/HEAD`. Only `<remote>/HEAD` is an alias;
    # treating every `*/HEAD` as one hides this branch from the census.
    "feature/HEAD": {"backend/app/head.py": "new\n"},
    # The branch whose replay cannot be read, once its blob is removed from the
    # object store. Its file is one main also changes, so the merge has to read
    # the blob to compare the two sides; removing it breaks this branch alone.
    "damaged": {"backend/app/x.py": "x-damaged\n"},
}

#: What main changes after the branches are cut.
MAIN_FILES = {
    "backend/app/shared.py": "one-MAIN\ntwo\nthree\n",
    "backend/app/x.py": "x-main\n",
    "backend/app/y.py": "y-main\n",
    "frontend/src/a11y-baseline.json": '{"v": "main"}\n',
}


class ConflictCensusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="conflict-census-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        # The machine's own git config is off the table: a global commit.gpgsign
        # or a different default branch name would red these tests for reasons
        # that have nothing to do with the census.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Test",
            GIT_COMMITTER_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_EMAIL="test@example.com",
        )
        self.branch_shas: dict[str, str] = {}
        self.build()

    # -- fixture ----------------------------------------------------------

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True,
            text=True, env=self.env,
        ).stdout

    def write(self, relative: str, contents: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def build(self) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True, env=self.env)
        for relative, contents in BASE_FILES.items():
            self.write(relative, contents)
        self.git("add", "-A")
        self.git("commit", "-qm", "chore: base")
        base = self.git("rev-parse", "HEAD").strip()

        for branch, files in BRANCHES.items():
            self.git("checkout", "-q", "-b", branch, base)
            for relative, contents in files.items():
                self.write(relative, contents)
            self.git("add", "-A")
            self.git("commit", "-qm", f"feat: work on {branch}")
            self.branch_shas[branch] = self.git("rev-parse", "HEAD").strip()

        self.git("checkout", "-q", "main")
        for relative, contents in MAIN_FILES.items():
            self.write(relative, contents)
        self.git("add", "-A")
        self.git("commit", "-qm", "feat: main moves")
        main_sha = self.main_sha = self.git("rev-parse", "HEAD").strip()

        # Remote-tracking refs, including the two a census must not treat as
        # branches to census: the base itself, and the `origin/HEAD` alias.
        self.git("update-ref", "refs/remotes/origin/main", main_sha)
        for branch, sha in self.branch_shas.items():
            if branch.endswith("/HEAD"):
                # `%(refname:short)` shortens `refs/remotes/origin/feature/HEAD`
                # to `origin/feature` — git's own shortening rule, which has
                # nothing to do with this census. The local branch is what
                # covers the `/HEAD` rule; no remote ref is made for it.
                continue
            self.git("update-ref", f"refs/remotes/origin/{branch}", sha)
        # The alias is a symbolic ref, the shape `git fetch` leaves behind.
        # Creating it as a plain ref would test a state no repository is in.
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    # -- the script -------------------------------------------------------

    def run_census(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.repo,
            capture_output=True, text=True, env=self.env, check=False,
        )

    def census_json(self, *args: str) -> dict:
        result = self.run_census("--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def contended_lines(self, stdout: str) -> list[str]:
        """The contended-files table, rows only, in the order printed."""
        head = "contended files, by how many branches conflict on them:\n"
        _, found, body = stdout.partition(head)
        self.assertTrue(found, f"no contended-files table in:\n{stdout}")
        rows = []
        for line in body.splitlines():
            if not line.startswith("  ") or line.lstrip().startswith("…"):
                break
            rows.append(line)
        return rows

    # -- tests ------------------------------------------------------------

    def test_reports_the_exact_conflict_set(self):
        out = self.census_json(
            "--base", "main", "clean", "conflict-a", "conflict-b", "mixed", "digits", "pair"
        )
        self.assertEqual(
            sorted(out["conflicted"]), ["conflict-a", "conflict-b", "digits", "mixed", "pair"]
        )
        self.assertEqual(out["clean"], ["clean"])
        self.assertEqual(out["conflicted"]["digits"], ["frontend/src/a11y-baseline.json"])
        self.assertEqual(out["conflicted"]["pair"], ["backend/app/x.py", "backend/app/y.py"])

    def test_a_file_git_merged_by_itself_is_not_a_conflict(self):
        # The trap only exists if git really does print the informational line
        # for this branch; without this probe the test would keep passing after
        # the fixture stopped being an auto-merge.
        probe = subprocess.run(
            ["git", "merge-tree", "--write-tree", "--name-only", "main", "mixed"],
            cwd=self.repo, capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(probe.returncode, 1, "the fixture must still be a conflicted merge")
        self.assertIn("Auto-merging backend/app/shared.py", probe.stdout)
        self.assertNotIn("CONFLICT (content): Merge conflict in backend/app/shared.py", probe.stdout)

        out = self.census_json("--base", "main", "mixed")
        self.assertEqual(out["conflicted"]["mixed"], ["backend/app/x.py"])
        self.assertNotIn("backend/app/shared.py", out["files"])

    def test_a_path_with_digits_is_not_dropped(self):
        out = self.census_json("--base", "main", "digits")
        self.assertEqual(list(out["files"]), ["frontend/src/a11y-baseline.json"])

    def test_a_contended_file_counts_branches_not_replays(self):
        out = self.census_json("--base", "main", "conflict-a", "conflict-b")
        self.assertEqual(out["files"]["backend/app/shared.py"], ["conflict-a", "conflict-b"])
        self.assertEqual(out["examined"], 2)

    def test_every_branch_examined_is_in_the_denominator(self):
        result = self.run_census("--base", "main", "clean", "conflict-a", "conflict-b")
        self.assertIn("examined 3 branches", result.stdout)
        self.assertIn("1 clean, 2 conflicted, 0 unreadable", result.stdout)

    def test_a_branch_name_with_a_slash_is_enumerated(self):
        out = self.census_json("--base", "main")
        self.assertIn("feature/nested", out["clean"])
        self.assertEqual(out["examined"], len(BRANCHES))

    def test_remote_tracking_branches_are_enumerated(self):
        # Every branch except the local-only `/HEAD` one, which `build()` gives
        # no remote ref.
        remotes = sorted(f"origin/{b}" for b in BRANCHES if not b.endswith("/HEAD"))
        out = self.census_json("--remotes")
        self.assertEqual(out["examined"], len(remotes))
        self.assertEqual(sorted(list(out["conflicted"]) + out["clean"]), remotes)
        self.assertIn("origin/conflict-a", out["conflicted"])

    def test_a_missing_base_is_not_reported_as_no_conflicts(self):
        result = self.run_census("--base", "no/such/branch", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "", "a census that could not be taken prints none")
        self.assertIn("cannot judge", result.stderr)

    def test_an_unresolvable_branch_is_reported_not_counted_clean(self):
        out = self.census_json("--base", "main", "clean", "typo-branch")
        self.assertEqual(out["unresolved"], ["typo-branch"])
        self.assertEqual(out["examined"], 1)
        self.assertEqual(out["clean"], ["clean"])

    def test_nothing_to_examine_is_not_a_census(self):
        result = self.run_census("--base", "main", "typo-branch")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot judge", result.stderr)
        # No enumeration happened, so the message must not claim to have
        # looked at one.
        self.assertIn("none of the branches named resolve", result.stderr)
        self.assertNotIn("local branches", result.stderr)

    def test_a_trimmed_file_list_says_how_many_it_hid(self):
        result = self.run_census(
            "--base", "main", "conflict-a", "conflict-b", "digits", "pair", "--top", "1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("and 3 more contended files", result.stdout)

    def test_the_shortlist_is_ranked_by_contention_not_by_path(self):
        # `a11y-baseline.json` is contended by two branches and sorts last of
        # the three contended paths; `x.py` and `y.py` are contended by one and
        # sort first. The header promises the order the trim uses.
        result = self.run_census("--base", "main", "digits", "digits-two", "pair", "--top", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.contended_lines(result.stdout)
        self.assertEqual(len(rows), 1, result.stdout)
        self.assertIn("frontend/src/a11y-baseline.json", rows[0])
        self.assertIn("and 2 more contended files", result.stdout)

    def test_the_whole_table_is_ranked_by_contention(self):
        result = self.run_census("--base", "main", "digits", "digits-two", "pair", "--top", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.contended_lines(result.stdout)
        self.assertEqual(len(rows), 3, result.stdout)
        self.assertIn("frontend/src/a11y-baseline.json", rows[0])
        self.assertIn("2  frontend/src/a11y-baseline.json", rows[0])
        # `--top 0` lists everything and therefore hides nothing.
        self.assertNotIn("more contended files", result.stdout)

    def test_a_remote_whose_own_name_has_a_slash_keeps_its_alias_out(self):
        # `git remote add a/b …` is legal, and then the alias sits at
        # `refs/remotes/a/b/HEAD` — one level deeper than `origin/HEAD`. A rule
        # that counts slashes drops it and censuses the alias as a branch.
        self.git("update-ref", "refs/remotes/weird/remote/main", self.main_sha)
        self.git("symbolic-ref", "refs/remotes/weird/remote/HEAD", "refs/remotes/weird/remote/main")

        out = self.census_json("--remotes")
        counted = sorted(list(out["conflicted"]) + out["clean"])
        self.assertNotIn("weird/remote", counted)
        self.assertIn("weird/remote/main", counted)

    def test_a_branch_named_slash_HEAD_is_a_branch_not_an_alias(self):
        out = self.census_json("--base", "main")
        self.assertIn("feature/HEAD", out["clean"])
        self.assertEqual(out["examined"], len(BRANCHES))

    def test_a_branch_the_replay_cannot_read_is_not_counted_clean(self):
        blob = self.git("rev-parse", "damaged:backend/app/x.py").strip()
        loose = self.repo / ".git" / "objects" / blob[:2] / blob[2:]
        self.assertTrue(
            loose.exists(),
            "the fixture has to hold this blob loose; once packed, deleting it proves nothing",
        )
        loose.unlink()

        probe = subprocess.run(
            ["git", "merge-tree", "--write-tree", "--name-only", "main", "damaged"],
            cwd=self.repo, capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertNotEqual(probe.returncode, 1, "an unreadable replay must not read as a conflict")
        self.assertIn("unable to read", probe.stderr + probe.stdout)

        out = self.census_json("--base", "main", "damaged", "clean")
        self.assertEqual(out["unreadable"], ["damaged"])
        self.assertEqual(out["clean"], ["clean"])
        self.assertEqual(out["files"], {}, "a branch that could not be read contributes no files")

        text = self.run_census("--base", "main", "damaged", "clean")
        self.assertIn("1 clean, 0 conflicted, 1 unreadable", text.stdout)
        self.assertIn("unreadable — cannot judge, not clean:", text.stdout)

    def test_a_root_that_is_not_a_directory_is_not_a_traceback(self):
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT), "--root", str(self.repo / "no-such-dir"),
                "--base", "main", "--json",
            ],
            cwd=self.repo, capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot judge", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_repository_with_no_conflicts_says_so(self):
        result = self.run_census("--base", "main", "clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no contended files", result.stdout)
        self.assertIn("1 clean, 0 conflicted, 0 unreadable", result.stdout)
        self.assertNotIn("conflicted branches:", result.stdout)


if __name__ == "__main__":
    unittest.main()
