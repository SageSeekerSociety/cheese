#!/usr/bin/env python3
"""bundle.py and its two hooks in pr-review.sh prepare, on a throwaway repository.

    python3 .claude/scripts/test_pr_review_bundle.py

  a) a delta round whose increment only merged main inherits the last conclusion: no
     reviewer, result.json written, post prints 增量自; a code increment does not.
  b) the call slices carry the call site of a touched function and no unrelated file.
  c) a type error on a changed line shows up in REVIEW.md's pyright section.

The stand-in `gh` answers prepare's reads; no network, no credentials. (c) runs against a
stand-in pyright that prints pyright's own JSON shape, and once more against the real one
when this machine has it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude/pr-review"))

import bundle  # noqa: E402

PR = "21"
GH = r"""#!/usr/bin/env bash
case "$*" in
  *.head.sha*) echo "$PR_HEAD" ;;
  *.base.ref*) echo main ;;
  *--paginate*/commits*) printf '%s\n' $PR_COMMITS ;;
  *'"# "'*) printf '# A title\n\nThe body.\n' ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""
#: pyright --outputjson's shape, reporting one error on line 3 of whatever file it is given.
FAKE_PYRIGHT = r"""#!/usr/bin/env python3
import json, os, sys
files = [a for a in sys.argv[1:] if not a.startswith("-")]
print(json.dumps({"generalDiagnostics": [
    {"file": os.path.abspath(f), "severity": "error", "message": "FAKE TYPE ERROR",
     "rule": "reportAssignmentType", "range": {"start": {"line": 2, "character": 0}}}
    for f in files]}))
"""

A_BASE = "def compute(x):\n    return x + 1\n\n\ndef unrelated_helper():\n    return 0\n"
A_PR = "def compute(x):\n    y: int = 'not an int'\n    return x + 2\n\n\ndef unrelated_helper():\n    return 0\n"
B = "from a import compute\n\n\ndef caller():\n    return compute(41)\n"
C = "def standalone_thing():\n    return 'nothing to do with compute_'\n"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class Bundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pr-review-bundle-"))
        origin, work = cls.tmp / "origin.git", cls.tmp / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            git(work, "config", k, v)
        (work / ".claude/scripts").mkdir(parents=True)
        shutil.copytree(REPO_ROOT / ".claude/pr-review", work / ".claude/pr-review",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(REPO_ROOT / ".claude/scripts/pr-review.sh", work / ".claude/scripts/")
        (work / "a.py").write_text(A_BASE)
        (work / "b.py").write_text(B)
        (work / "c.py").write_text(C)
        (work / ".test-run-id").write_text(uuid.uuid4().hex + "\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "base")
        git(work, "remote", "add", "origin", str(origin))
        git(work, "push", "-q", "origin", "main")
        cls.base = git(work, "rev-parse", "HEAD")
        # The PR: one code change, reviewed; then main moves and the PR merges it.
        git(work, "checkout", "-q", "-b", "pr")
        (work / "a.py").write_text(A_PR)
        git(work, "commit", "-qam", "change compute")
        cls.reviewed = git(work, "rev-parse", "HEAD")
        git(work, "checkout", "-q", "main")
        (work / "elsewhere.py").write_text("VALUE = 1\n")
        git(work, "add", "elsewhere.py")
        git(work, "commit", "-qm", "main moves on")
        git(work, "push", "-q", "origin", "main")
        git(work, "checkout", "-q", "pr")
        git(work, "merge", "-q", "--no-edit", "main")
        cls.merged = git(work, "rev-parse", "HEAD")
        # A second increment that changes code: §4 re-reviews it.
        (work / "b.py").write_text(B + "\n\ndef more():\n    return compute(1)\n")
        git(work, "commit", "-qam", "more code")
        cls.code = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR}/head")
        git(work, "checkout", "-q", "main")
        git(work, "fetch", "-q", "origin")
        cls.work = work
        cls.bin = cls.tmp / "bin"
        cls.bin.mkdir()
        (cls.bin / "gh").write_text(GH)
        (cls.bin / "gh").chmod(0o755)
        cls.fake_pyright = cls.tmp / "fake-pyright"
        cls.fake_pyright.write_text(FAKE_PYRIGHT)
        cls.fake_pyright.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_script(self, root, head, *args, extra=None):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "PR_HEAD": head,
               "PR_COMMITS": " ".join((self.reviewed, self.merged, self.code)),
               "PR_REVIEW_DIR": str(self.tmp / root), "PYRIGHT": str(self.fake_pyright),
               **(extra or {})}
        (self.tmp / root).mkdir(exist_ok=True)
        return subprocess.run(["bash", str(self.work / ".claude/scripts/pr-review.sh"), *args],
                              capture_output=True, text=True, env=env)

    def first_round(self, root, findings=()):
        out = self.run_script(root, self.reviewed, "prepare", PR, self.reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        d = Path(out.stdout.strip()).parent
        (d / "result.json").write_text(json.dumps({
            "reviewed_sha": self.reviewed, "summary": "s", "candidates_found": len(findings),
            "candidates_refuted": 0, "not_covered": [],
            "findings": [{"severity": s, "kind": "runtime", "file": "a.py", "line": 2, "title": "t",
                          "failure": "f", "evidence": "e", "rung": 4, "confirm_with": ""}
                         for s in findings]}))
        post = self.run_script(root, self.reviewed, "post", str(d))
        self.assertEqual(post.returncode, 0, post.stderr)
        return d, Path(out.stdout.strip()).read_text()

    # ---------------------------------------------------------------- a) inherit
    def test_a_merge_only_increment_inherits_without_a_reviewer(self):
        self.first_round("inherit")
        out = self.run_script("inherit", self.merged, "prepare", PR, self.merged, "--since", self.reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
        text = review_md.read_text()
        self.assertIn("## Inherited conclusion", text)
        self.assertIn("Do not\nstart any subagent", text)
        self.assertNotIn("## Bundle", text, "an inherited round hands the finders nothing")
        result = json.loads((review_md.parent / "result.json").read_text())
        self.assertEqual(result["reviewed_sha"], self.merged)
        self.assertEqual(result["findings"], [])
        post = self.run_script("inherit", self.merged, "post", str(review_md.parent))
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertTrue(post.stdout.splitlines()[0].startswith(
            f"独立 review：head {self.merged[:8]}（增量自 {self.reviewed[:8]}）结论 无 blocker/major"),
            post.stdout.splitlines()[0])

    def test_a_code_increment_is_reviewed(self):
        self.first_round("code")
        out = self.run_script("code", self.code, "prepare", PR, self.code, "--since", self.reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        text = Path(out.stdout.strip()).read_text()
        self.assertNotIn("## Inherited conclusion", text)
        self.assertIn("## Incremental review", text)
        self.assertIn("## Bundle", text)
        self.assertFalse((Path(out.stdout.strip()).parent / "result.json").exists())

    def test_a_previous_round_with_a_major_is_not_inherited(self):
        self.first_round("major", findings=("major",))
        out = self.run_script("major", self.merged, "prepare", PR, self.merged, "--since", self.reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("## Inherited conclusion", Path(out.stdout.strip()).read_text())
        self.assertIn("blocker/major", out.stderr)

    def test_a_without_the_previous_result_nothing_is_inherited(self):
        result, why = bundle.inherit(str(self.work), "main", self.reviewed, self.merged, "")
        self.assertIsNone(result)
        self.assertIn("没有上一轮", why)

    # ----------------------------------------------------------------- b) slices
    def test_b_slices_carry_the_call_site_and_no_unrelated_file(self):
        text = "\n".join(bundle.section_slices(str(self.work), self.base, self.reviewed))
        self.assertIn("### 改动 `compute`（a.py:1）", text)
        self.assertIn("`b.py:5`", text)
        self.assertIn("return compute(41)", text)
        self.assertNotIn("c.py", text)
        self.assertNotIn("unrelated_helper", text, "an untouched function is not sliced")

    def test_b_a_deleted_function_lists_who_still_calls_it(self):
        work = self.work
        git(work, "checkout", "-q", "-b", "delete-compute", self.reviewed)
        try:
            (work / "a.py").write_text("def unrelated_helper():\n    return 0\n")
            git(work, "commit", "-qam", "drop compute")
            head = git(work, "rev-parse", "HEAD")
        finally:
            git(work, "checkout", "-q", "main")
        text = "\n".join(bundle.section_slices(str(work), self.reviewed, head))
        self.assertIn("### 删除 `compute`", text)
        self.assertIn("`b.py:5`", text)

    # ---------------------------------------------------------------- c) pyright
    def test_c_a_type_error_reaches_review_md(self):
        out = self.run_script("pyright", self.reviewed, "prepare", PR, self.reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        text = Path(out.stdout.strip()).read_text()
        self.assertIn("## B2. 单文件 pyright", text)
        self.assertIn("⚠ `a.py:3` FAKE TYPE ERROR（reportAssignmentType）", text)

    def test_c_real_pyright_reports_the_bad_assignment(self):
        cmd = bundle.find_pyright(str(REPO_ROOT), None)
        if not cmd:
            self.skipTest("no pyright on this machine; the stand-in test above still runs")
        tree = self.tmp / "tree-real"
        git(self.work, "worktree", "add", "-q", "--detach", str(tree), self.reviewed)
        try:
            text = "\n".join(bundle.section_pyright(str(self.work), str(tree), self.base, self.reviewed,
                                                    " ".join(cmd)))
        finally:
            git(self.work, "worktree", "remove", "--force", str(tree))
        self.assertIn("⚠ `a.py:2`", text)
        self.assertIn("reportAssignmentType", text)


if __name__ == "__main__":
    unittest.main()
