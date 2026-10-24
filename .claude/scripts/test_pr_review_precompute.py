#!/usr/bin/env python3
"""precompute.py against throwaway git repositories.

    python3 .claude/scripts/test_pr_review_precompute.py

One case reproduces a real incident: #4259 (deleted BulkUpdateRequest and left
NotificationUpdate with a single reference). It has a mutant twin: the same
assertion run against precompute.py with the check cut out, which must fail, or
the case would pass for a tool that finds nothing.

prepare no longer runs tests: CI on GitHub runs them, the seat running the review
runs whatever experiment a finding needs, and a subagent only reads. So the
sections here are the symbol scan and the PR-body number recompute.
"""

import importlib.util
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "pr-review" / "precompute.py"


def load(text=None):
    mod = types.ModuleType("precompute_under_test")
    mod.__file__ = str(SRC)
    exec(compile(text if text is not None else SRC.read_text(), str(SRC), "exec"), mod.__dict__)
    return mod


def mutant(old, new):
    text = SRC.read_text()
    assert old in text, f"mutation anchor vanished: {old!r}"
    return load(text.replace(old, new, 1))


def git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x", *args],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def commit(repo, files, msg):
    for rel, text in files.items():
        f = Path(repo) / rel
        if text is None:
            f.unlink()
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


SCHEMAS = "class NotificationUpdate:\n    pass\n\nclass BulkUpdateRequest:\n    items: list[NotificationUpdate]\n"
ROUTES = "from schemas import BulkUpdateRequest\n\ndef bulk(r: BulkUpdateRequest):\n    return r\n"


class Precompute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "r"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")

    # ---- #4259: orphaned symbol
    def orphan_case(self, mod):
        base = commit(self.repo, {"backend/app/schemas.py": SCHEMAS, "backend/app/routes.py": ROUTES}, "base")
        head = commit(self.repo, {"backend/app/schemas.py": "class NotificationUpdate:\n    pass\n",
                                  "backend/app/routes.py": "def bulk(r):\n    return r\n"}, "drop bulk")
        return "\n".join(mod.section_symbols(self.repo, base, head))

    def test_orphan_symbol_warned(self):
        out = self.orphan_case(load())
        self.assertIn("⚠ `NotificationUpdate`", out)
        self.assertIn("删除 `BulkUpdateRequest`", out)

    def test_orphan_scan_mutant_is_red(self):
        out = self.orphan_case(mutant("if len(now) != 1 or", "if True or"))
        with self.assertRaises(AssertionError):
            self.assertIn("⚠ `NotificationUpdate`", out)

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is not installed here")
    def test_orphan_found_through_the_tree_when_ripgrep_counts(self):
        # The reviewer found this one: rg without --no-filename prints `path:NAME`,
        # so every count came back 0 and the orphan scan always said 无. The test
        # gives section_symbols the tree, which is the rg path prepare takes.
        base = commit(self.repo, {"backend/app/schemas.py": SCHEMAS, "backend/app/routes.py": ROUTES}, "base")
        head = commit(self.repo, {"backend/app/schemas.py": "class NotificationUpdate:\n    pass\n",
                                  "backend/app/routes.py": "def bulk(r):\n    return r\n"}, "drop bulk")
        out = "\n".join(load().section_symbols(self.repo, base, head, tree=self.repo))
        self.assertIn("⚠ `NotificationUpdate`", out)
        # The deliberate violation: drop --no-filename and the counts go back to 0,
        # so the same call finds nothing. That is what this test is here to catch.
        broken = mutant('"--no-filename", ', '')
        out = "\n".join(broken.section_symbols(self.repo, base, head, tree=self.repo))
        self.assertNotIn("⚠ `NotificationUpdate`", out)

    def test_a_failed_git_grep_is_not_a_section_of_zeros(self):
        # A scan that fails must not come back as all-zero counts: zero is the
        # answer the orphan scan reads as "no other user", so a failure would
        # report every orphan as not-an-orphan. No `tree` here on purpose — it
        # takes the git grep branch, which is the one CI runs, where ripgrep is
        # not installed. An unknown revision makes git grep exit 128.
        # One load: each call makes a fresh module, so the exception class in a
        # second load is a different object and assertRaises would not match it.
        mod = load()
        with self.assertRaises(mod.ScanFailed):
            mod.count_refs(self.repo, "no-such-rev", ["helper"])
        # `refs()` is what the orphan confirmation and the per-symbol rows read,
        # so the same rule has to hold there: an empty list from a failed scan
        # drops the orphan (len(now) != 1) and prints "0 处命中" as a fact.
        with self.assertRaises(mod.ScanFailed):
            mod.refs(self.repo, "no-such-rev", "helper")
        # The deliberate violation: turn the exit-status check back off and the
        # same call returns zeros instead of raising. The anchor names the git
        # grep branch, so it stays unique whichever branch runs here.
        broken = mutant('if p.returncode not in (0, 1):\n            raise ScanFailed(f"git grep',
                        'if False:\n            raise ScanFailed(f"git grep')
        self.assertEqual(broken.count_refs(self.repo, "no-such-rev", ["helper"]), {"helper": 0})

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is not installed here")
    def test_a_failed_ripgrep_is_not_a_section_of_zeros(self):
        # The same rule on the ripgrep branch: exit 2 (an unreadable tree) must
        # raise, not read as "no matches". Skipped where rg is absent, because
        # then this branch is not the one taken.
        mod = load()
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "not-a-tree"
            with self.assertRaises(mod.ScanFailed):
                mod.count_refs(self.repo, "HEAD", ["helper"], tree=missing)
        broken = mutant('raise ScanFailed(f"rg exited {p.returncode}',
                        'return _match_counts(p.stdout, names)  # raise ScanFailed(f"rg exited {p.returncode}')
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "not-a-tree"
            self.assertEqual(broken.count_refs(self.repo, "HEAD", ["helper"], tree=missing),
                             {"helper": 0})

    def test_patch_points_listed(self):
        base = commit(self.repo, {"a.py": "def helper():\n    pass\n",
                                  "t.py": "def test(monkeypatch):\n    monkeypatch.setattr(a, 'helper', 1)\n"}, "b")
        head = commit(self.repo, {"a.py": "def helper():\n    return 1\n"}, "chg")
        out = "\n".join(load().section_symbols(self.repo, base, head))
        self.assertIn("打桩点", out)
        self.assertIn("t.py", out)

    def test_orphan_in_untouched_file(self):
        base = commit(self.repo, {"a.py": "def helper():\n    pass\n", "b.py": "from a import helper\nhelper()\n"}, "b")
        head = commit(self.repo, {"b.py": "y = 1\n"}, "drop use")
        out = "\n".join(load().section_symbols(self.repo, base, head))
        self.assertIn("⚠ `helper`（a.py）", out)

    def test_orphan_found_beyond_three_hundred_public_names(self):
        many = "".join(f"def f{i}():\n    pass\n" for i in range(320))
        base = commit(self.repo, {"big.py": many, "a.py": "def helper():\n    pass\n",
                                  "b.py": "from a import helper\nhelper()\n"}, "b")
        head = commit(self.repo, {"big.py": many + "# touched\n", "b.py": "y = 1\n"}, "drop use")
        out = "\n".join(load().section_symbols(self.repo, base, head))
        self.assertIn("⚠ `helper`（a.py）", out)

    def test_common_names_only_counted(self):
        mod = load()
        mod.COMMON_HITS = 1
        base = commit(self.repo, {"a.py": "def helper():\n    pass\n", "b.py": "helper()\n"}, "b")
        head = commit(self.repo, {"a.py": "def helper():\n    return 1\n"}, "chg")
        self.assertIn("名字太常见", "\n".join(mod.section_symbols(self.repo, base, head)))

    def test_orphan_with_name_repeated_on_definition_line(self):
        base = commit(self.repo, {"a.py": 'UserId = NewType("UserId", int)\n', "b.py": "x: UserId\n",
                                  "c.py": "def helper():  # helper\n    pass\n"}, "b")
        head = commit(self.repo, {"b.py": "x = 1\n", "c.py": "def helper():\n    pass\n"}, "chg")
        out = "\n".join(load().section_symbols(self.repo, base, head))
        self.assertIn("⚠ `UserId`（a.py）", out)
        self.assertNotIn("⚠ `helper`", out)

    # ---- the whole run writes both sections, and runs no test
    def test_main_writes_all_sections_and_runs_no_tests(self):
        base = commit(self.repo, {"a.py": "x = 1\n"}, "b")
        head = commit(self.repo, {"a.py": "x = 2\n"}, "c")
        scripts = Path(self.tmp.name) / "s"
        scripts.mkdir()
        (scripts / "prove-regression.py").write_text("print('PROVE-STUB')\n")
        (scripts / "recompute-pr-claims.py").write_text("import sys\nprint('CLAIMS-STUB', sys.argv[1:])\n")
        body = Path(self.tmp.name) / "pr.md"
        body.write_text("# t\n\n改了 1 个文件\n")
        out = Path(self.tmp.name) / "P.md"
        load().main(["--repo", str(self.repo), "--base", base, "--head", head, "--pr", "1", "--pr-body", str(body),
                     "--scripts", str(scripts), "--out", str(out)])
        text = out.read_text()
        for needle in ("## 1.", "## 2.", "CLAIMS-STUB"):
            self.assertIn(needle, text)
        # Tests belong to CI and to the seat's own experiments, not to prepare.
        self.assertNotIn("PROVE-STUB", text)


if __name__ == "__main__":
    unittest.main()
