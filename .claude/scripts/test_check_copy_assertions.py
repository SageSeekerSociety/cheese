#!/usr/bin/env python3
"""Self-test check-copy-assertions.py.

The examples below are the guard's whole claim: a whole-sentence assertion on
rendered copy is reported, a part assertion and an exempted line are not. The
mutation test then makes the guard laxer in two ways and asserts these same
examples would go red, so the examples prove the guard still bites rather than
merely agreeing with a guard that reports everything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check-copy-assertions.py")
REPO_ROOT = SCRIPT.parents[2]
FIXTURE = "frontend/src/example.spec.ts"

#: (说明, 测试片段, 是否应报)。改判据时先改这份表。
EXAMPLES: tuple[tuple[str, str, bool], ...] = (
    (
        "中文整句，notice 读数",
        "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
        True,
    ),
    (
        "英文整句，渲染出来的 textContent",
        "expect(el?.textContent?.trim()).toBe('Save failed: HTTP 500')",
        True,
    ),
    (
        "左值只有一个名字时取它本身",
        "expect(message).toBe('Request failed with status 500')",
        True,
    ),
    (
        "双引号、toEqual 也是相等断言",
        'expect(refusal.message).toEqual("Only platform admins can do this")',
        True,
    ),
    (
        "不带插值的模板字面量也是整句",
        "expect(notice.textContent).toBe(`本月的芝士额度已用完，11月1日重置。`)",
        True,
    ),
    (
        "resolves.toBe 也认",
        "await expect(notice.textContent).resolves.toBe('Only platform admins can do this')",
        True,
    ),
    (
        "同一行第二条断言也看",
        "expect(label.textContent).toBe('保存'); "
        "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
        True,
    ),
    (
        "钉部件：只断一个数字",
        "expect(note).toContain('11月1日')",
        False,
    ),
    (
        "钉部件：模板字面量本来就从常量取数",
        "expect(note).toContain(`${BASE_MS * MOTION_FACTOR.slow}ms`)",
        False,
    ),
    (
        "带插值的模板字面量不是整句",
        "expect(notice).toBe(`本月还有 ${left} 次额度，${reset} 重置。`)",
        False,
    ),
    (
        "短标签不是一句话",
        "expect(label.textContent).toBe('保存')",
        False,
    ),
    (
        "路径不是文案",
        "expect(link.textContent).toBe('/projects/7/topics/3?tab=files')",
        False,
    ),
    (
        "标识符不是文案",
        "expect(heading.textContent).toBe('workspace-project')",
        False,
    ),
    (
        "数据字段不是在读文案：一整句话也不报",
        "expect(result).toBe('A whole sentence that is long enough to count.')",
        False,
    ),
    (
        "注释掉的断言不算",
        "// expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
        False,
    ),
    (
        "块注释里的断言不算",
        "/*\nexpect(notice).toBe('本月的芝士额度已用完，11月1日重置。')\n*/",
        False,
    ),
    (
        "断言和字面量跨行时不猜",
        "expect(el.textContent)\n  .toBe('Save failed: HTTP 500')",
        False,
    ),
)


class CopyAssertionGuardTests(unittest.TestCase):
    def test_examples_are_judged_as_the_guard_claims(self) -> None:
        for name, source, should_report in EXAMPLES:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    root = self._fixture(directory, source)
                    result = self._run(SCRIPT, root)
                    if should_report:
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertIn("整句断言", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_test_suffix_is_scanned(self) -> None:
        source = "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')"
        for suffix in (".spec.ts", ".spec.tsx", ".test.ts", ".test.tsx"):
            with self.subTest(suffix=suffix):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory) / "tree"
                    self._write(root, f"frontend/src/example{suffix}", source)
                    result = self._run(SCRIPT, root)
                    self.assertEqual(result.returncode, 1, result.stderr)

    def test_exemption_marker_clears_the_line_and_the_line_above(self) -> None:
        for name, source in (
            (
                "同一行",
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。') "
                "// copy-assertions: allow — 这句话本身就是被测的东西",
            ),
            (
                "上一行",
                "// copy-assertions: allow — 服务器原话要原样透传\n"
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            ),
        ):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    root = self._fixture(directory, source)
                    result = self._run(SCRIPT, root)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_exemption_marker_elsewhere_in_the_file_does_not_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(
                directory,
                "// copy-assertions: allow — 只豁免它下面那一行\n"
                "\n"
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            result = self._run(SCRIPT, root)
            self.assertEqual(result.returncode, 1, result.stderr)

    def test_only_test_files_under_frontend_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "frontend/src/component.ts",
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            self._write(
                root,
                "frontend/node_modules/pkg/thing.spec.ts",
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            result = self._run(SCRIPT, root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_baseline_only_shrinks(self) -> None:
        source = "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')"
        sentence = "本月的芝士额度已用完，11月1日重置。"
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory, source)
            baseline = Path(directory) / "baseline.json"
            baseline.write_text("[]", encoding="utf-8")
            result = self._run(SCRIPT, root, "--baseline", str(baseline))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("新增 1 处", result.stderr)

            baseline.write_text(
                json.dumps([[FIXTURE, sentence]], ensure_ascii=False),
                encoding="utf-8",
            )
            result = self._run(SCRIPT, root, "--baseline", str(baseline))
            self.assertEqual(result.returncode, 0, result.stderr)

            baseline.write_text(
                json.dumps(
                    [
                        [FIXTURE, sentence],
                        ["frontend/src/gone.spec.ts", "已经删掉的那一句。"],
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self._run(SCRIPT, root, "--baseline", str(baseline))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("删掉这一行", result.stderr)

    def test_the_baseline_is_keyed_by_file_and_literal(self) -> None:
        sentence = "本月的芝士额度已用完，11月1日重置。"
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(
                directory,
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            baseline = Path(directory) / "baseline.json"
            # 同一个文件、另一句话：文件级匹配会把它误当成已冻结。
            baseline.write_text(
                json.dumps([[FIXTURE, "另一句同样长的话，已经被改掉了。"]], ensure_ascii=False),
                encoding="utf-8",
            )
            result = self._run(SCRIPT, root, "--baseline", str(baseline))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("新增 1 处", result.stderr)

            # 同一句在树里出现两次，基线只有一条：第二条是新增。
            self._write(
                root,
                FIXTURE,
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')\n"
                "expect(a.notice.value).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            baseline.write_text(
                json.dumps([[FIXTURE, sentence]], ensure_ascii=False),
                encoding="utf-8",
            )
            result = self._run(SCRIPT, root, "--baseline", str(baseline))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("新增 1 处", result.stderr)

    def test_json_names_the_new_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(
                directory,
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            result = self._run(SCRIPT, root, "--json")
            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["new"]), 1)
            self.assertEqual(payload["new"][0]["path"], FIXTURE)
            self.assertEqual(payload["stale"], [])

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "以 root 跑时 chmod 挡不住读取，判不了这个场景",
    )
    def test_a_directory_it_cannot_read_is_not_reported_as_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(
                directory,
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            locked = root / "frontend/src/locked"
            locked.mkdir()
            self._write(
                root,
                "frontend/src/locked/locked.spec.ts",
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')",
            )
            locked.chmod(0o000)
            try:
                result = self._run(SCRIPT, root)
            finally:
                locked.chmod(0o755)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("通过", result.stdout)

    def test_a_file_that_is_not_utf8_is_not_reported_as_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tree"
            target = root / FIXTURE
            target.parent.mkdir(parents=True)
            target.write_bytes(
                "expect(notice).toBe('本月的芝士额度已用完，11月1日重置。')".encode(
                    "utf-16"
                )
            )
            result = self._run(SCRIPT, root)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("通过", result.stdout)

    def test_repository_tree_matches_its_frozen_baseline(self) -> None:
        result = self._run(SCRIPT, REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("冻结", result.stdout)

        dumped = self._run(SCRIPT, REPO_ROOT, "--dump")
        self.assertEqual(dumped.returncode, 0, dumped.stderr)
        self.assertIn("共 ", dumped.stdout)

    def test_a_loosened_or_silenced_guard_turns_this_self_test_red(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        anchor = 'if __name__ == "__main__":'
        self.assertIn(anchor, source)
        mutations = {
            "判据放宽：凡相等断言都报": (
                "is_sentence = lambda _literal: True\n"
                "reads_copy = lambda _subject: True\n"
            ),
            "判据失效：一处都不报": "reads_copy = lambda _subject: False\n",
        }
        for name, override in mutations.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    mutated = Path(directory) / SCRIPT.name
                    mutated.write_text(
                        source.replace(anchor, override + anchor), encoding="utf-8"
                    )
                    self._assert_examples_go_red(mutated)

    def _assert_examples_go_red(self, guard: Path) -> None:
        wrong = 0
        for _, source, should_report in EXAMPLES:
            with tempfile.TemporaryDirectory() as directory:
                root = self._fixture(directory, source)
                result = self._run(guard, root)
                if result.returncode not in (0, 1):
                    # 变异把守卫弄崩了，那不算「判对了」，也不算「判错了」。
                    wrong += 1
                    continue
                if (result.returncode == 1) != should_report:
                    wrong += 1
        self.assertGreater(wrong, 0)

    @staticmethod
    def _fixture(directory: str, source: str) -> Path:
        root = Path(directory) / "tree"
        CopyAssertionGuardTests._write(root, FIXTURE, source)
        return root

    @staticmethod
    def _write(root: Path, relative: str, source: str) -> None:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source + "\n", encoding="utf-8")

    @staticmethod
    def _run(
        script: Path, root: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--root", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
