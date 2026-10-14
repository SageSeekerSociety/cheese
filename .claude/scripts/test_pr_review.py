#!/usr/bin/env python3
"""pr-review.sh against a throwaway repository and a stand-in `gh`.

    python3 .claude/scripts/test_pr_review.py

The stand-in `gh` answers the few `gh api` reads prepare makes and appends every call to a
log, so the test can say what was and was not asked of GitHub. No network, no credentials.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PR = "7"

GH = r"""#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$*" in
  *.head.sha*) echo "$PR_HEAD" ;;
  *.base.ref*) echo main ;;
  *--paginate*/commits*) printf '%s\n' $PR_COMMITS ;;
  *'"# "'*) printf '# A title\n\nThe body.\n' ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def result_json(sha, *severities):
    return {
        "reviewed_sha": sha,
        "summary": "Summary.",
        "candidates_found": 2,
        "candidates_refuted": 1,
        "findings": [
            {
                "severity": sev,
                "title": f"a {sev} thing",
                "file": "f.txt",
                "line": 1,
                "kind": "runtime",
                "rung": 4,
                "failure": "it breaks",
                "evidence": "ran it",
                "confirm_with": "",
            }
            for sev in severities
        ],
        "not_covered": [],
    }


class PrReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pr-review-test-"))
        origin, work = cls.tmp / "origin.git", cls.tmp / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            git(work, "config", k, v)
        (work / ".claude/scripts").mkdir(parents=True)
        shutil.copytree(REPO_ROOT / ".claude/pr-review", work / ".claude/pr-review")
        shutil.copy(REPO_ROOT / ".claude/scripts/pr-review.sh", work / ".claude/scripts/")
        (work / "f.txt").write_text("base\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "base")
        git(work, "remote", "add", "origin", str(origin))
        git(work, "push", "-q", "origin", "main")
        cls.shas = []
        for n in (1, 2, 3):
            (work / "f.txt").write_text(f"change {n}\n")
            git(work, "commit", "-qam", f"change {n}")
            cls.shas.append(git(work, "rev-parse", "HEAD"))
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR}/head")
        # A commit of the PR that is not an ancestor of its head (a force-push left it behind).
        git(work, "checkout", "-q", "-b", "side", cls.shas[0])
        (work / "f.txt").write_text("side\n")
        git(work, "commit", "-qam", "side")
        cls.side = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/side")
        git(work, "checkout", "-q", "main")
        git(work, "fetch", "-q", "origin")
        cls.work = work

        cls.bin = cls.tmp / "bin"
        cls.bin.mkdir()
        (cls.bin / "gh").write_text(GH)
        (cls.bin / "gh").chmod(0o755)
        cls.log = cls.tmp / "gh.log"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_script(self, *args):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_LOG": str(self.log),
            "PR_HEAD": self.shas[-1],
            "PR_COMMITS": " ".join([*self.shas, self.side]),
            "PR_REVIEW_DIR": str(self.tmp / "reviews"),
        }
        (self.tmp / "reviews").mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(self.work / ".claude/scripts/pr-review.sh"), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def gh_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def prepare(self, *args):
        out = self.run_script("prepare", PR, *args)
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
        return review_md, review_md.read_text()

    def post(self, review_md, *severities, dry=False):
        d = review_md.parent
        meta = dict(
            line.split("=", 1) for line in (d / "meta").read_text().splitlines() if "=" in line
        )
        sha = meta["sha"].strip("'")
        (d / "result.json").write_text(json.dumps(result_json(sha, *severities)))
        before = self.gh_calls()
        out = self.run_script("post", str(d), *(["--dry-run"] if dry else []))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.gh_calls(), before, "post must not call gh")
        return d, out.stdout

    def test_post_writes_the_result_and_never_calls_gh(self):
        review_md, text = self.prepare()
        self.assertNotIn("Incremental review", text)
        d, out = self.post(review_md, "minor")
        saved = (d / "REVIEW-RESULT.md").read_text()
        self.assertEqual(saved, out)
        self.assertEqual(
            saved.splitlines()[0], f"独立 review：head {self.shas[-1][:8]} 结论 无 blocker/major"
        )
        shown = self.run_script("show", str(d), "--line")
        self.assertEqual(shown.stdout, saved.splitlines()[0] + "\n")
        self.assertEqual(self.run_script("show", str(d)).stdout, saved)
        self.assertFalse(any("comment" in c for c in self.gh_calls()))

    def test_dry_run_prints_without_writing(self):
        review_md, _ = self.prepare()
        d, out = self.post(review_md, "major", dry=True)
        self.assertIn("有 1 条", out.splitlines()[0])
        self.assertFalse((d / "REVIEW-RESULT.md").exists())

    def test_incremental_review_is_scoped_to_the_increment(self):
        since = self.shas[0]
        review_md, text = self.prepare("--since", since[:10])
        self.assertIn("## Incremental review", text)
        self.assertIn(f"{since[:12]}..{self.shas[-1][:12]}", text)
        self.assertIn(f"git diff {since[:12]} HEAD", text)
        self.assertIn(f"delta since {since}", text)
        d, out = self.post(review_md, "blocker", "major", "minor")
        self.assertEqual(
            out.splitlines()[0],
            f"独立 review：head {self.shas[-1][:8]}（增量自 {since[:8]}）结论 有 2 条",
        )

    def test_incremental_review_picks_up_the_last_rounds_result(self):
        first, _ = self.prepare(self.shas[0])
        self.post(first, "major")
        _, text = self.prepare("--since", self.shas[0])
        self.assertTrue((first.parent.parent / f"pr-review-{PR}-{self.shas[-1][:12]}.input/previous.md").exists())
        self.assertIn("previous.md", text)

    def test_since_must_be_an_older_commit_on_the_same_line(self):
        for since, why in ((self.side, "not an ancestor"), (self.shas[-1], "already the head")):
            out = self.run_script("prepare", PR, "--since", since)
            self.assertEqual(out.returncode, 1)
            self.assertIn(why, out.stderr)

    def test_usage_errors(self):
        for args in (
            ("prepare", PR, "--since"),
            ("prepare", PR, "--bogus"),
            ("post",),
            ("show", "d", "--bogus"),
        ):
            self.assertEqual(self.run_script(*args).returncode, 2, args)


if __name__ == "__main__":
    unittest.main()
