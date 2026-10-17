#!/usr/bin/env python3
"""Black-box contract tests; run with python3 .claude/scripts/test_recompute_pr_claims.py.

RECOMPUTE_CLAIMS_SCRIPT selects a candidate CLI for mutation checking. The normal
suite also runs the required wrong-file-count assertion against an injected
implementation that copies its counts from the prose, and requires it to fail.
All evidence and git repositories live in temporary directories.
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest


REAL_SCRIPT = Path(__file__).with_name("recompute-pr-claims.py").resolve()
SCRIPT = Path(os.environ.get("RECOMPUTE_CLAIMS_SCRIPT", REAL_SCRIPT)).resolve()
FOUR_FILES = b"2\t1\ta.py\n3\t0\tb.py\n0\t4\tc.py\n1\t0\td.py\n"
JUNIT = """<testsuites tests="4" failures="1" errors="1" skipped="1">
<testsuite tests="4"><testcase name="ok"/><testcase name="bad"><failure/></testcase>
<testcase name="oops"><error/></testcase><testcase name="skip"><skipped/></testcase>
</testsuite></testsuites>"""


class ClaimsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="recompute-claims-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.body = self.root / "body.md"
        self.stat = self.root / "diff.numstat"
        self.report = self.root / "tests.xml"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                        GIT_TERMINAL_PROMPT="0")

    def check(self, body, *, numstat=FOUR_FILES, junit=None, extra=()):
        self.body.write_text(body, encoding="utf-8")
        command = [sys.executable, "-I", "-B", str(SCRIPT), "--text", str(self.body)]
        if numstat is not None:
            self.stat.write_bytes(numstat)
            command += ["--numstat", str(self.stat)]
        if junit is not None:
            self.report.write_text(junit, encoding="utf-8")
            command += ["--junit", str(self.report)]
        return subprocess.run([*command, *extra], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=30)

    def assert_status(self, result, expected):
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_wrong_file_count_is_red(self):
        result = self.check("改了 3 个文件")
        self.assert_status(result, 1)
        self.assertIn("files: claimed=3 measured=4", result.stdout)
        self.assertIn("MISMATCH", result.stdout)

    def test_agreeing_chinese_claims(self):
        result = self.check("修改 **4** 个文件；新增 6 行，删除 5 行。")
        self.assert_status(result, 0)
        for metric, count in (("files", 4), ("added", 6), ("deleted", 5)):
            self.assertIn(f"{metric}: claimed={count} measured={count}", result.stdout)

    def test_english_and_thousands_separators(self):
        result = self.check("1 file changed, 1,234 insertions(+), 10 deletions(-)",
                            numstat=b"1234\t10\tname with spaces\n")
        self.assert_status(result, 0)
        self.assertIn("added: claimed=1234 measured=1234", result.stdout)

    def test_sentence_punctuation_after_label_count(self):
        self.assert_status(self.check("files: 4, tests: 4.", junit=JUNIT), 0)
        result = self.check("files: 3. tests: 4.", junit=JUNIT)
        self.assert_status(result, 1)
        self.assertIn("files: claimed=3 measured=4", result.stdout)

    def test_each_occurrence_is_checked(self):
        result = self.check("文件数：4\nfiles: 3\n新增 5 行；删除 4 行")
        self.assert_status(result, 1)
        self.assertEqual(result.stdout.count("MISMATCH"), 3)
        self.assertIn("body.md:2 files: claimed=3 measured=4", result.stdout)

    def test_both_body_and_commit_are_checked(self):
        commit = self.root / "commit.txt"
        commit.write_text("files: 3", encoding="utf-8")
        result = self.check("files: 4", extra=("--text", str(commit)))
        self.assert_status(result, 1)
        self.assertIn("commit.txt:1", result.stdout)

    def test_empty_diff_is_zero(self):
        self.assert_status(self.check("文件数：0；新增 0 行；删除 0 行", numstat=b""), 0)
        self.assert_status(self.check("文件数：1", numstat=b""), 1)

    def test_binary_and_renames(self):
        evidence = b"-\t-\timage.png\0" + b"2\t1\t\0old\tname\n.py\0new\tname\n.py\0"
        result = self.check("files: 2; 新增 2 行; 删除 1 行", numstat=evidence)
        self.assert_status(result, 0)
        result = self.check("files: 2", numstat=b"-\t-\timage.png\n0\t0\told => new\n")
        self.assert_status(result, 0)

    def test_invalid_stats_are_unverified(self):
        for data in (b"4 files changed\n", b"1\t2\tx", b"-\t2\tx\n", b"1\t2\t\n",
                     b"1\t2\tx\n1\t2\tx\n", b"0\t0\t\0old\0", b"\n"):
            with self.subTest(data=data):
                self.assert_status(self.check("files: 4", numstat=data), 2)

    def test_no_recognized_claim_is_not_green(self):
        for text in ("PR #123 fixes FB-45", "新增 3 个测试", "改了 3.5 个文件",
                     "files: -4", "files: 1,23", ""):
            with self.subTest(text=text):
                self.assert_status(self.check(text), 2)

    def test_missing_evidence_is_not_green(self):
        self.assert_status(self.check("files: 4", numstat=None), 2)
        result = self.check("files: 4; 测试通过：7")
        self.assert_status(result, 2)
        self.assertIn("missing evidence", result.stdout)

    def test_mismatch_takes_priority_over_missing_evidence(self):
        self.assert_status(self.check("files: 3; tests: 7"), 1)

    def test_junit_outcomes_recomputed_from_cases(self):
        result = self.check("测试总数：4；通过 1 个测试；测试失败：1；测试跳过：1；测试错误：1",
                            numstat=None, junit=JUNIT)
        self.assert_status(result, 0)
        for metric, count in (("tests", 4), ("passed", 1), ("failed", 1),
                              ("skipped", 1), ("errors", 1)):
            self.assertIn(f"{metric}: claimed={count} measured={count}", result.stdout)

    def test_junit_english_and_incorrect_pass_count(self):
        for text in ("Ran 4 tests; 1 test passed; 1 test failed; 1 test skipped; 1 test errors",
                     "tests: 4; tests passed: 1; tests failed: 1; tests skipped: 1; tests errors: 1"):
            with self.subTest(text=text):
                self.assert_status(self.check(text, numstat=None, junit=JUNIT), 0)
        result = self.check("tests passed: 4", numstat=None, junit=JUNIT)
        self.assert_status(result, 1)
        self.assertIn("passed: claimed=4 measured=1", result.stdout)

    def test_junit_invalid_empty_and_summary_only_rejected(self):
        for xml in ("<testsuite>", "<html/>", '<testsuite tests="4"/>',
                    '<testsuite tests="0"/>', '<testsuite tests="1"><testcase>',
                    '<testsuite tests="-1"><testcase/></testsuite>',
                    '<testsuite failures="1"><testcase/></testsuite>',
                    '<testsuite><testcase><failure/><skipped/></testcase></testsuite>'):
            with self.subTest(xml=xml):
                self.assert_status(self.check("tests: 4", numstat=None, junit=xml), 2)

    def test_nested_suites_and_multiple_failure_details_count_once(self):
        xml = ('<testsuites tests="2"><testsuite tests="2" failures="1">'
               '<testsuite><testcase><failure/><failure/></testcase></testsuite>'
               '<testcase/></testsuite></testsuites>')
        self.assert_status(self.check("tests: 2; tests failed: 1; tests passed: 1",
                                      numstat=None, junit=xml), 0)

    def test_missing_inputs_and_exclusive_sources(self):
        self.assert_status(self.check("files: 4", extra=("--text", str(self.root / "absent"))), 2)
        self.assert_status(self.check("files: 4", extra=("--base", "HEAD")), 2)
        self.assert_status(self.check("files: 4", numstat=None,
                                       extra=("--repo", str(self.root), "--base", "HEAD")), 2)

    def test_real_git_diff_counts_committed_changes_since_merge_base(self):
        repo = self.root / "repo"
        repo.mkdir()

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  env=self.env, capture_output=True, text=True).stdout.strip()

        def commit(message):
            git("-c", "user.name=Claims Test", "-c", "user.email=claims@example.invalid",
                "-c", "core.hooksPath=/dev/null", "commit", "-m", message)

        git("init", "-q", "--template=", "--initial-branch=main")
        (repo / "old.py").write_text("original\n", encoding="utf-8")
        git("add", "old.py")
        commit("base")
        base = git("rev-parse", "HEAD")
        git("checkout", "-b", "feature")
        git("mv", "old.py", "new.py")
        (repo / "add.py").write_text("new\nnew\n", encoding="utf-8")
        (repo / "binary").write_bytes(b"\0binary")
        (repo / "odd\tname\n.py").write_text("new\n", encoding="utf-8")
        git("add", "add.py", "binary", "odd\tname\n.py")
        commit("feature")
        head = git("rev-parse", "HEAD")
        git("checkout", "-b", "base-advanced", base)
        (repo / "base-only").write_text("not in the PR\n", encoding="utf-8")
        git("add", "base-only")
        commit("advance base independently")
        (repo / "old.py").write_text("uncommitted change\n", encoding="utf-8")
        extra = ("--repo", str(repo), "--base", "base-advanced", "--head", head)
        result = self.check("files: 4; 新增 3 行; 删除 0 行", numstat=None, extra=extra)
        self.assert_status(result, 0)
        result = self.check("改了 3 个文件", numstat=None, extra=extra)
        self.assert_status(result, 1)
        self.assertIn("files: claimed=3 measured=4", result.stdout)
        result = self.check("files: 4", numstat=None,
                            extra=("--repo", str(repo), "--base", "missing-ref"))
        self.assert_status(result, 2)

    def test_self_test_rejects_a_checker_that_copies_prose_counts(self):
        mutant = self.root / "copy-prose.py"
        mutant.write_text(
            "import runpy, sys\nfrom pathlib import Path\n"
            f"ns = runpy.run_path({str(REAL_SCRIPT)!r})\n"
            "body = Path(sys.argv[sys.argv.index('--text') + 1]).read_text()\n"
            "copied = {metric: count for _, metric, count, _ in ns['extract_claims'](body)}\n"
            "ns['main'].__globals__['parse_numstat'] = lambda data: copied\n"
            "sys.exit(ns['main']())\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
             "ClaimsTests.test_wrong_file_count_is_red"],
            env={**os.environ, "RECOMPUTE_CLAIMS_SCRIPT": str(mutant)}, cwd=self.root,
            capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FAIL: test_wrong_file_count_is_red", result.stderr)
        self.assertIn("0 != 1", result.stderr)


if __name__ == "__main__":
    unittest.main()
