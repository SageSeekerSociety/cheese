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
    # Three conflicted files in one branch, so the report cannot be a
    # one-file-per-branch assumption.
    "pair": {
        "backend/app/x.py": "x-branch\n",
        "backend/app/y.py": "y-branch\n",
    },
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
        main_sha = self.git("rev-parse", "HEAD").strip()

        # Remote-tracking refs, including the two a census must not treat as
        # branches to census: the base itself, and the `origin/HEAD` alias.
        self.git("update-ref", "refs/remotes/origin/main", main_sha)
        for branch, sha in self.branch_shas.items():
            self.git("update-ref", f"refs/remotes/origin/{branch}", sha)
        self.git("update-ref", "refs/remotes/origin/HEAD", main_sha)

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
        out = self.census_json("--remotes")
        self.assertEqual(out["examined"], len(BRANCHES))
        self.assertEqual(
            sorted(list(out["conflicted"]) + out["clean"]),
            sorted(f"origin/{branch}" for branch in BRANCHES),
        )
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

    def test_a_trimmed_file_list_says_how_many_it_hid(self):
        result = self.run_census(
            "--base", "main", "conflict-a", "conflict-b", "digits", "pair", "--top", "1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("and 3 more contended files", result.stdout)


if __name__ == "__main__":
    unittest.main()
