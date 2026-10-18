#!/usr/bin/env python3
"""precompute.py against throwaway git repositories.

    python3 .claude/scripts/test_pr_review_precompute.py

Two of the cases reproduce real incidents: #4259 (deleted BulkUpdateRequest and left
NotificationUpdate with a single reference) and #4268 x #4259 (a baseline guard that is
green alone and red once the deletion lands beside it). Each has a mutant twin: the same
assertion run against precompute.py with the check cut out, which must fail, or the case
would pass for a tool that finds nothing.
"""

import importlib.util
import json
import subprocess
import sys
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
GUARD = '''_BASELINE = set()
import re, pathlib, sys
root = pathlib.Path(__file__).resolve().parents[1] / "src"
text = "\\n".join(p.read_text() for p in root.glob("*.py"))
bad = [n for n in re.findall(r"^class (\\w+)", text, re.M)
       if len(re.findall(rf"\\b{n}\\b", text)) < 2 and n not in _BASELINE]
print("unreferenced:", bad)
sys.exit(1 if bad else 0)
'''


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
        out = self.orphan_case(mutant("if len(now) != 1:", "if True:"))
        with self.assertRaises(AssertionError):
            self.assertIn("⚠ `NotificationUpdate`", out)

    def test_patch_points_listed(self):
        base = commit(self.repo, {"a.py": "def helper():\n    pass\n",
                                  "t.py": "def test(monkeypatch):\n    monkeypatch.setattr(a, 'helper', 1)\n"}, "b")
        head = commit(self.repo, {"a.py": "def helper():\n    return 1\n"}, "chg")
        out = "\n".join(load().section_symbols(self.repo, base, head))
        self.assertIn("打桩点", out)
        self.assertIn("t.py", out)

    # ---- #4268 x #4259: parallel baseline guard
    def collision_case(self, mod):
        commit(self.repo, {"src/a.py": "class Helper:\n    pass\n", "src/b.py": "from a import Helper\nx = Helper()\n"}, "main")
        base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-qb", "x")
        head = commit(self.repo, {"tests/test_guard.py": GUARD}, "add guard")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-qb", "y", base)
        commit(self.repo, {"src/b.py": "y = 1\n"}, "drop use")
        git(self.repo, "checkout", "-q", "main")
        return "\n".join(mod.section_trial(self.repo, base, head, 4268, "", "main", [("#4259", "y")],
                                           f"{sys.executable} {{files}}"))

    def test_parallel_guard_failure_written(self):
        out = self.collision_case(load())
        self.assertIn("最新 main + 本 PR：**通过**", out)
        self.assertIn("最新 main + #4259 + 本 PR：**失败**", out)
        self.assertIn("unreferenced: ['Helper']", out)

    def test_trial_mutant_is_red(self):
        out = self.collision_case(mutant('("通过" if code == 0 else "失败")', '"通过"'))
        with self.assertRaises(AssertionError):
            self.assertIn("#4259 + 本 PR：**失败**", out)

    def test_no_trial_without_guard_or_mention(self):
        base = commit(self.repo, {"a.py": "x = 1\n"}, "b")
        head = commit(self.repo, {"a.py": "x = 2\n"}, "c")
        out = "\n".join(load().section_trial(self.repo, base, head, 1, "plain", "main", [], None))
        self.assertIn("不做", out)

    # ---- ci:fast report
    def test_ci_report_split(self):
        commit(self.repo, {"a.py": "x = 1\n"}, "b")
        rep = Path(self.tmp.name) / "rep.json"
        rep.write_text(json.dumps({"status": "pass", "tier": "quick",
                                   "ran": [{"id": "ruff", "result": "pass"}, {"id": "mypy", "result": "fail"}],
                                   "not_run": [{"id": "e2e", "reason": "超时"}]}))
        out = "\n".join(load().section_ci(self.repo, self.repo, str(rep)))
        self.assertIn("已 pass（1 项", out)
        self.assertIn("`ruff`", out)
        self.assertIn("`mypy` fail", out)
        self.assertIn("`e2e`（超时）", out)

    def test_ci_report_missing_says_so(self):
        commit(self.repo, {"a.py": "x = 1\n"}, "b")
        out = "\n".join(load().section_ci(self.repo, self.repo, None))
        self.assertIn("没有找到", out)

    # ---- whole run writes the file, helpers called
    def test_main_writes_all_sections(self):
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
                     "--scripts", str(scripts), "--main-ref", "main", "--out", str(out)])
        text = out.read_text()
        for needle in ("## 1.", "## 2.", "## 3.", "## 4.", "PROVE-STUB", "CLAIMS-STUB"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
