#!/usr/bin/env python3
"""Self-tests for check-review-card-body.py, including mutation verification."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orchestrator_autoaccept  # noqa: E402

REAL_SCRIPT = Path(__file__).with_name("check-review-card-body.py").resolve()
SCRIPT = Path(os.environ.get("CHECK_REVIEW_CARD_BODY_SCRIPT", REAL_SCRIPT)).resolve()


def _load_guard(path: Path = REAL_SCRIPT):
    spec = importlib.util.spec_from_file_location("check_review_card_body", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReviewCardBodyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="review-card-body-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.body_file = self.root / "card-body.md"
        existing = os.environ.get("PYTHONPATH", "")
        py_path = (
            f"{SCRIPTS_DIR}{os.pathsep}{existing}" if existing else str(SCRIPTS_DIR)
        )
        self.env = {**os.environ, "PYTHONPATH": py_path}

    def run_cli(
        self, body: str, *, script: Path = SCRIPT, via_file: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if via_file:
            self.body_file.write_text(body, encoding="utf-8")
            cmd = [sys.executable, str(script), "--text", str(self.body_file)]
        else:
            cmd = [sys.executable, str(script), "--body", body]
        return subprocess.run(
            cmd, check=False, capture_output=True, text=True, env=self.env, timeout=30
        )

    def test_imports_review_line_from_orchestrator_autoaccept(self) -> None:
        guard = _load_guard()
        self.assertIs(guard.REVIEW_LINE, orchestrator_autoaccept.REVIEW_LINE)

    def test_first_round_valid_body_passes(self) -> None:
        body = (
            "## 改动原因\n"
            "新增验收卡独立 review 结论行位置守卫。\n\n"
            "## 验证说明\n"
            "四类正文自测通过。\n\n"
            "独立 review：head 11111111 结论 无 blocker/major｜档位 B\n"
        )
        result = self.run_cli(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok:", result.stdout)

    def test_review_line_not_on_last_line_fails(self) -> None:
        for body in (
            (
                "独立 review：head 11111111 结论 无 blocker/major｜档位 B\n\n"
                "## 改动原因\n"
                "把结论行放到了正文开头。\n"
            ),
            (
                "## 改动原因\n"
                "把整份报告贴在后面，导致最后一行不是结论行。\n\n"
                "独立 review：head 11111111 结论 无 blocker/major｜档位 B\n\n"
                "**Independent review** of `11111111`\n"
            ),
        ):
            with self.subTest(body=body):
                result = self.run_cli(body)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("未放在正文最后一行", result.stderr)

    def test_first_round_with_since_fails(self) -> None:
        body = (
            "## 改动原因\n"
            "首轮审查误加了增量自字段。\n\n"
            "独立 review：head 22222222（增量自 11111111）结论 无 blocker/major｜档位 B\n"
        )
        result = self.run_cli(body)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "首轮独立 review 结论行不应包含「增量自 11111111」", result.stderr
        )

    def test_multi_round_valid_body_passes(self) -> None:
        body = (
            "## 改动原因\n"
            "修复首轮发现的问题并完成增量复审。\n\n"
            "独立 review：head 11111111 结论 有 1 条｜档位 B\n"
            "独立 review：head 22222222（增量自 11111111）结论 无 blocker/major｜档位 B\n"
        )
        result = self.run_cli(body, via_file=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok:", result.stdout)

    def test_missing_or_empty_body_fails(self) -> None:
        for body in ("", "   \n\n", "## 改动原因\n只有说明没有结论行。\n"):
            with self.subTest(body=body):
                result = self.run_cli(body)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("未找到独立 review 结论行", result.stderr)

    def test_inline_self_test_flag_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--self-test"],
            check=False,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)

    def review_result(self, head: str, verdict: str = "无 blocker/major") -> Path:
        """A rendered review beside the card, as `pr-review.sh post` leaves it."""
        path = self.root / "REVIEW-RESULT.md"
        path.write_text(
            f"独立 review：head {head} 结论 {verdict}｜档位 B\n\n**Independent review**\n",
            encoding="utf-8",
        )
        return path

    def run_script(
        self, *args: str, script: Path = SCRIPT, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            check=False,
            capture_output=True,
            text=True,
            env=self.env,
            cwd=None if cwd is None else str(cwd),
            timeout=60,
        )

    def test_fix_adds_a_missing_last_line(self) -> None:
        self.body_file.write_text("## 改动原因\n修复问题。\n", encoding="utf-8")
        result = self.run_script(
            "--fix",
            "--review-result",
            str(self.review_result("11111111")),
            "--text",
            str(self.body_file),
            "--out",
            str(self.body_file),
            "--head",
            "11111111",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        check = self.run_script("--head", "11111111", "--text", str(self.body_file))
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_fix_repairs_a_wrong_head_and_moves_the_line_to_the_end(self) -> None:
        self.body_file.write_text(
            "独立 review：head 99999999 结论 无 blocker/major｜档位 B\n\n"
            "## 改动原因\n修复问题。\n",
            encoding="utf-8",
        )
        result = self.run_script(
            "--fix",
            "--review-result",
            str(self.review_result("11111111")),
            "--text",
            str(self.body_file),
            "--out",
            str(self.body_file),
            "--head",
            "11111111",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.body_file.read_text(encoding="utf-8").splitlines()[-1],
            "独立 review：head 11111111 结论 无 blocker/major｜档位 B",
        )
        check = self.run_script("--head", "11111111", "--text", str(self.body_file))
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_check_rejects_a_head_that_is_not_the_delivery_head(self) -> None:
        body = "独立 review：head 11111111 结论 无 blocker/major｜档位 B\n"
        result = self.run_script("--head", "22222222", "--body", body)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("不是当前 head", result.stderr)

    def _git(self, repo: Path, *args: str) -> str:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    def _repo_with_an_exempt_increment(self) -> tuple[Path, str, str]:
        """A repo where the branch head is the reviewed commit plus a merge of main."""
        repo = self.root / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        for key, value in (
            ("user.email", "t@example.com"),
            ("user.name", "t"),
            ("commit.gpgsign", "false"),
        ):
            self._git(repo, "config", key, value)
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        self._git(repo, "add", "base.txt")
        self._git(repo, "commit", "-qm", "base")
        self._git(repo, "checkout", "-qb", "topic")
        (repo / "code.txt").write_text("code\n", encoding="utf-8")
        self._git(repo, "add", "code.txt")
        self._git(repo, "commit", "-qm", "the change under review")
        reviewed = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "checkout", "-q", "main")
        (repo / "main.txt").write_text("main\n", encoding="utf-8")
        self._git(repo, "add", "main.txt")
        self._git(repo, "commit", "-qm", "main moves")
        self._git(
            repo,
            "update-ref",
            "refs/remotes/origin/main",
            self._git(repo, "rev-parse", "HEAD"),
        )
        self._git(repo, "checkout", "-q", "topic")
        self._git(repo, "merge", "-q", "--no-edit", "main", "-m", "merge main")
        return repo, reviewed, self._git(repo, "rev-parse", "HEAD")

    def test_stamp_declares_an_exempt_increment(self) -> None:
        repo, reviewed, head = self._repo_with_an_exempt_increment()
        self.body_file.write_text("卡片正文。\n", encoding="utf-8")
        result = self.run_script(
            "--stamp",
            "--review-result",
            str(self.review_result(reviewed[:8])),
            "--head",
            head,
            "--root",
            str(repo),
            "--base",
            "main",
            "--text",
            str(self.body_file),
            "--out",
            str(self.body_file),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = self.body_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines[-1],
            f"独立 review：head {head[:8]}（增量自 {reviewed[:8]}）"
            "结论 无 blocker/major｜档位 B",
        )
        check = self.run_script("--head", head, "--text", str(self.body_file))
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_stamp_refuses_an_increment_that_needs_review(self) -> None:
        repo, reviewed, _ = self._repo_with_an_exempt_increment()
        (repo / "code.txt").write_text("code changed\n", encoding="utf-8")
        self._git(repo, "add", "code.txt")
        self._git(repo, "commit", "-qm", "a real change after the review")
        head = self._git(repo, "rev-parse", "HEAD")
        self.body_file.write_text("卡片正文。\n", encoding="utf-8")
        result = self.run_script(
            "--stamp",
            "--review-result",
            str(self.review_result(reviewed[:8])),
            "--head",
            head,
            "--root",
            str(repo),
            "--base",
            "main",
            "--text",
            str(self.body_file),
            "--out",
            str(self.body_file),
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺一次增量审", result.stderr)

    def test_fixing_to_an_old_head_makes_the_check_red(self) -> None:
        """Deliberate violation: pin the old sha instead of the delivery head.

        A --fix that both pins a fixed head and skips the increment gate is what
        a careless implementation looks like; the check has to catch the line it
        writes."""
        target = 'head = args.head or source["head"]'
        gate = '_refuse_unreviewed_head(source["head"], target_head, root, base)'
        source = REAL_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(target, source)
        self.assertIn(gate, source)
        mutant = self.root / "check-review-card-body-old-head.py"
        mutant.write_text(
            source.replace(target, 'head = "12345678"').replace(gate, "pass"),
            encoding="utf-8",
        )

        body = self.root / "mutant-body.md"
        body.write_text("## 改动原因\n修复问题。\n", encoding="utf-8")
        result = self.run_script(
            "--fix",
            "--review-result",
            str(self.review_result("11111111")),
            "--text",
            str(body),
            "--out",
            str(body),
            script=mutant,
            # CI runs the helper tests from the repository root, not from
            # .claude/scripts; a copied script has to find the review method
            # from there too.
            cwd=SCRIPTS_DIR.parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("head 12345678", body.read_text(encoding="utf-8"))

        check = self.run_script("--head", "11111111", "--text", str(body))
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("不是当前 head", check.stderr)

    def test_fix_refuses_a_head_the_review_did_not_cover(self) -> None:
        self.body_file.write_text("## 改动原因\n修复问题。\n", encoding="utf-8")
        result = self.run_script(
            "--fix",
            "--review-result",
            str(self.review_result("11111111")),
            "--head",
            "22222222",
            "--text",
            str(self.body_file),
            "--out",
            str(self.body_file),
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("缺一次增量审", result.stderr)

    def test_stamp_keeps_a_delta_reviews_scope_when_the_head_did_not_move(self) -> None:
        result_file = self.root / "REVIEW-RESULT.md"
        result_file.write_text(
            "独立 review：head 22222222（增量自 11111111）结论 无 blocker/major｜档位 B\n",
            encoding="utf-8",
        )
        self.body_file.write_text(
            "## 改动原因\n修复问题。\n\n"
            "独立 review：head 22222222 结论 无 blocker/major｜档位 B\n",
            encoding="utf-8",
        )
        result = self.run_script(
            "--stamp",
            "--review-result",
            str(result_file),
            "--head",
            "22222222",
            "--text",
            str(self.body_file),
            "--out",
            str(self.body_file),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.body_file.read_text(encoding="utf-8")
        self.assertIn("head 22222222（增量自 11111111）", text)
        check = self.run_script("--head", "22222222", "--text", str(self.body_file))
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_fix_without_a_review_file_keeps_the_tier(self) -> None:
        body = self.root / "solo" / "body.md"
        body.parent.mkdir()
        body.write_text(
            "## 改动原因\n修复问题。\n\n"
            "独立 review：head 11111111 结论 有 2 条｜档位 B\n",
            encoding="utf-8",
        )
        result = self.run_script("--fix", "--text", str(body), "--out", str(body))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "独立 review：head 11111111 结论 有 2 条｜档位 B",
            body.read_text(encoding="utf-8"),
        )

    def test_removing_last_line_check_makes_not_on_last_line_case_fail(self) -> None:
        """Deliberate violation: strip the last-line check and assert the test goes red."""
        source = REAL_SCRIPT.read_text(encoding="utf-8")
        target = "last_match_lineno != last_lineno"
        self.assertIn(target, source)
        broken = source.replace(target, "False")
        mutant = self.root / "check-review-card-body-mutant.py"
        mutant.write_text(broken, encoding="utf-8")

        probe = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "ReviewCardBodyGuardTests.test_review_line_not_on_last_line_fails",
            ],
            env={**self.env, "CHECK_REVIEW_CARD_BODY_SCRIPT": str(mutant)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertNotEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        self.assertNotIn("ModuleNotFoundError", probe.stderr)
        self.assertIn("FAIL: test_review_line_not_on_last_line_fails", probe.stderr)
        self.assertIn("0 != 1", probe.stderr)


if __name__ == "__main__":
    unittest.main()
