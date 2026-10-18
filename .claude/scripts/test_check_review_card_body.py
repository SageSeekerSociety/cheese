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

    def run_cli(
        self, body: str, *, script: Path = SCRIPT, via_file: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if via_file:
            self.body_file.write_text(body, encoding="utf-8")
            cmd = [sys.executable, str(script), "--text", str(self.body_file)]
        else:
            cmd = [sys.executable, str(script), "--body", body]
        return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=30)

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
        self.assertIn("首轮独立 review 结论行不应包含「增量自 11111111」", result.stderr)

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
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)

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
            env={**os.environ, "CHECK_REVIEW_CARD_BODY_SCRIPT": str(mutant)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertNotEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        self.assertIn("FAIL: test_review_line_not_on_last_line_fails", probe.stderr)


if __name__ == "__main__":
    unittest.main()
