#!/usr/bin/env python3
"""Prove the agent-definition guard fires, and that this repository passes it.

Two jobs, and they are different questions:

- `test_this_repository_passes` runs the guard against the tree it lives in. The guard
  cannot be trusted to judge the repository unless it is run against the repository, and
  this is the half that checks the repository rather than only the fixtures — the half a
  runner that collects `.claude/scripts/test_*.py` turns into a gate.
- The rest build a throwaway tree per rule, each violating exactly one, and assert the
  exit code and that the message names the fix. A guard whose fixtures were never seen
  red is a guard that passes whether the rule holds or not.

`test_a_tier_widened_past_the_cap_is_refused_even_with_its_own_self_test_moved_along`
is why this guard exists next to `tiers.py --self-test` instead of duplicating it: it
mutates the real tiering module the way an author would — the table and the expectation
inside it both widened — shows the module's own self-test still green, and shows this
guard red. A check whose expected value lives in the file under check can be moved out of
the way; one that compares against the environment's cap cannot.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-agent-defs.py")
REPO = SCRIPT.parents[2]
REAL_TIERS = REPO / ".claude" / "pr-review" / "tiers.py"
GOOD_TIERS = REAL_TIERS.read_text(encoding="utf-8")

GOOD_DEFINITION = """---
name: check-runner
description: >
  Run the checks and report a pass or fail per hook.
tools: Bash, Read
model: sonnet
---

Run them.
"""


class AgentDefinitionGuardTests(unittest.TestCase):
    def test_this_repository_passes(self) -> None:
        result = self._run(REPO)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("通过", result.stdout)

    def test_a_well_formed_tree_passes(self) -> None:
        """The guard has an accepting half. A guard that rejects everything proves nothing
        by rejecting the fixtures below."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            result = self._run(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("通过", result.stdout)

    def test_a_quoted_value_and_an_inline_comment_are_legal(self) -> None:
        """`model: "sonnet"` and `model: sonnet  # why` name the same alias in YAML. Reading
        the line as raw text would report a definition that is written correctly."""
        self._accept_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("model: sonnet", 'model: "sonnet"  # the Claude alias'),
        )
        self._accept_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("name: check-runner", "name: check-runner  # the slug"),
        )

    def test_a_quoted_block_marker_is_a_description(self) -> None:
        """`description: ">"` is that one character, not a block header: unlike the
        unquoted form above it carries a description, and reporting it would refuse a
        definition that is written correctly."""
        self._accept_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("description: >\n  Run the checks and report a pass or fail per hook.\n",
                                    'description: ">"\n'),
        )

    def test_a_doubled_single_quote_is_part_of_the_name(self) -> None:
        """YAML spells one quote inside a single-quoted scalar as two, so
        `name: 'check-runner'''` is `check-runner'` — not a slug, and not the file's stem
        either, however much the text before the last quote looks like it."""
        self._reject_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("name: check-runner", "name: 'check-runner'''"),
            "不是 slug",
        )

    def test_an_empty_quoted_description_says_nothing(self) -> None:
        """A quoted value is text even when it looks like a block header, but an empty one
        is still no description — the two rules have to hold at once."""
        self._reject_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("description: >\n  Run the checks and report a pass or fail per hook.\n",
                                    "description: ''\n"),
            "description",
        )

    def test_a_byte_order_mark_before_the_fence_is_still_frontmatter(self) -> None:
        self._accept_definition("check-runner.md", chr(0xFEFF) + GOOD_DEFINITION)

    def test_a_missing_frontmatter_is_reported(self) -> None:
        self._reject_definition("check-runner.md", "Just a body.\n", "frontmatter")

    def test_a_missing_name_is_reported(self) -> None:
        """`name` is the subagent_type the parent passes, so a definition without one
        cannot be started — the rule the guard exists for, and it needs its own fixture."""
        self._reject_definition(
            "check-runner.md",
            "---\ndescription: Run the checks.\nmodel: sonnet\n---\n\nBody.\n",
            "name",
        )

    def test_a_name_that_is_not_the_file_stem_is_reported(self) -> None:
        self._reject_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("name: check-runner", "name: check-runner-two"),
            "check-runner.md",
        )

    def test_a_name_that_is_not_a_slug_is_reported(self) -> None:
        self._reject_definition(
            "CheckRunner.md",
            GOOD_DEFINITION.replace("name: check-runner", "name: Check Runner"),
            "slug",
        )

    def test_a_missing_description_is_reported(self) -> None:
        self._reject_definition(
            "check-runner.md",
            "---\nname: check-runner\nmodel: sonnet\n---\n\nBody.\n",
            "description",
        )

    def test_an_empty_description_block_is_not_a_description(self) -> None:
        """`description: >` with nothing under it is the shape a deleted line leaves
        behind. It reads as present to anything that looks for the key alone."""
        self._reject_definition(
            "check-runner.md",
            "---\nname: check-runner\ndescription: >\nmodel: sonnet\n---\n\nBody.\n",
            "description",
        )

    def test_an_indented_block_marker_with_an_indicator_is_not_a_description(self) -> None:
        """`description: |2` is a block header like `>`, indicator and all: with nothing
        indented under it there is no description."""
        self._reject_definition(
            "check-runner.md",
            "---\nname: check-runner\ndescription: |2\nmodel: sonnet\n---\n\nBody.\n",
            "description",
        )

    def test_a_name_fix_never_suggests_a_name_that_is_not_a_slug(self) -> None:
        """`CheckRunner.md` is not fixable by naming the stem — the stem is not a slug
        either — so the message has to say the file name changes too."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write(
                root, "agents/CheckRunner.md",
                GOOD_DEFINITION.replace("name: check-runner", "name: Check Runner"),
            )
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("name: CheckRunner", result.stderr)
            # The message body, not the `path:line:` prefix the line already carries,
            # which would satisfy a bare mention of the file name.
            self.assertIn("这个名字和文件名", result.stderr)

    def test_a_definition_that_is_not_utf8_is_reported_with_the_others(self) -> None:
        """One unreadable file must not turn the run into a traceback that hides every
        other finding, nor into a silent pass."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write_bytes(
                root, "agents/broken.md",
                b"---\nname: broken\ndescription: \xff\xfe\n---\n",
            )
            self._write(
                root, "agents/gateway.md",
                GOOD_DEFINITION.replace("name: check-runner", "name: gateway")
                .replace("model: sonnet", "model: kimi-k3"),
            )
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(".claude/agents/broken.md:", result.stderr)
            self.assertIn("UTF-8", result.stderr)
            self.assertIn(".claude/agents/gateway.md:", result.stderr)

    def test_a_gateway_model_in_the_model_field_is_reported(self) -> None:
        """The dispatched-to model must be named as a subagent_type, not in `model`."""
        self._reject_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("model: sonnet", "model: kimi-k3"),
            "subagent_type",
        )

    def test_a_reserved_model_filename_is_reported(self) -> None:
        """The fixture keeps `name` equal to the stem, so the only rule it breaks is the
        reserved file name. With `name: kimi-k3` in a file called `model-kimi-k3.md` the
        name-versus-stem rule fires too, and the assertion would pass on that one."""
        self._reject_definition(
            "model-kimi-k3.md",
            GOOD_DEFINITION.replace("name: check-runner", "name: model-kimi-k3"),
            "平台生成的模型目录",
        )

    def test_a_reserved_file_name_is_not_offered_as_the_fix(self) -> None:
        """A `model-*` file with a bad `name` gets two findings, and the hint of the first
        must not name the stem the second one refuses: following it leaves the file red."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write(
                root, "agents/model-zz.md",
                GOOD_DEFINITION.replace("name: check-runner", "name: Bad Name"),
            )
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("name: model-zz", result.stderr)
            self.assertIn("平台生成的模型目录", result.stderr)

    def test_a_reserved_file_name_with_no_name_is_not_offered_as_the_fix(self) -> None:
        """The missing-name hint shares the reserved-name rule's variable, and this file
        has no `name` at all: naming its stem as the one to add would be refused by the
        second finding."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write(
                root, "agents/model-zz.md",
                GOOD_DEFINITION.replace("name: check-runner\n", ""),
            )
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("name: model-zz", result.stderr)
            self.assertIn("缺 `name`", result.stderr)
            self.assertIn("平台生成的模型目录", result.stderr)

    def test_an_empty_tools_line_is_reported(self) -> None:
        self._reject_definition(
            "check-runner.md",
            "---\nname: check-runner\ndescription: Run the checks.\ntools:\n---\n\nBody.\n",
            "tools",
        )

    def test_two_definitions_with_one_name_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write(root, "agents/check-runner-two.md", GOOD_DEFINITION)
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("重名", result.stderr)

    def test_a_missing_agents_directory_cannot_be_judged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "pr-review/tiers.py", GOOD_TIERS)
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(".claude/agents", result.stderr)

    def test_a_missing_tiering_module_cannot_be_judged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "agents/check-runner.md", GOOD_DEFINITION)
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("tiers.py", result.stderr)

    def test_a_tier_widened_past_the_cap_is_refused(self) -> None:
        self._reject_tiers(_widened_once(), "4")

    def test_a_tier_widened_past_the_cap_is_refused_even_with_its_own_self_test_moved_along(
        self,
    ) -> None:
        """The mutation an author actually makes: widen a stage, and move the expectation
        in the same file so its own self-test stays green. The module agrees with itself;
        this guard, reading the cap instead, does not."""
        mutated = _widened_once()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiers.py"
            path.write_text(mutated, encoding="utf-8")
            own = subprocess.run(
                [sys.executable, str(path), "--self-test"],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(own.returncode, 0, own.stdout + own.stderr)
        self._reject_tiers(mutated, "4")

    def test_a_widened_tier_is_seen_even_right_after_a_run(self) -> None:
        """The same-size edit pinned to the same second is exactly what a bytecode cache
        would answer from the older file. The guard reads the source, so it goes red."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            first = self._run(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            tiers = root / ".claude" / "pr-review" / "tiers.py"
            stamp = tiers.stat().st_mtime
            tiers.write_text(_widened_same_size(), encoding="utf-8")
            os.utime(tiers, (stamp, stamp))
            second = self._run(root)
            self.assertNotEqual(second.returncode, 0, second.stdout)

    def test_the_guard_writes_no_bytecode_cache_into_the_tree(self) -> None:
        """It reads `tiers.py`; nothing it reads should leave a file behind in the tree it
        is checking."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            result = self._run(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([path for path in root.rglob("__pycache__")], [])

    def _accept_definition(self, name: str, source: str) -> None:
        """The other half of a fixture: a definition the guard has to let through."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write(root, f"agents/{name}", source)
            result = self._run(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def _reject_definition(self, name: str, source: str, hint: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write(root, f"agents/{name}", source)
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(f".claude/agents/{name}:", result.stderr)
            self.assertIn(hint, result.stderr)

    def _reject_tiers(self, source: str, hint: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tree(root)
            self._write(root, "pr-review/tiers.py", source)
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(".claude/pr-review/tiers.py", result.stderr)
            self.assertIn(hint, result.stderr)

    @staticmethod
    def _tree(root: Path) -> None:
        """A tree that passes, for a fixture to break one rule in."""
        AgentDefinitionGuardTests._write(root, "agents/check-runner.md", GOOD_DEFINITION)
        AgentDefinitionGuardTests._write(root, "pr-review/tiers.py", GOOD_TIERS)

    @staticmethod
    def _write(root: Path, relative: str, source: str) -> None:
        target = root / ".claude" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    @staticmethod
    def _write_bytes(root: Path, relative: str, source: bytes) -> None:
        target = root / ".claude" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)

    @staticmethod
    def _run(root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            check=False, capture_output=True, text=True,
        )


def _widened_same_size() -> str:
    """Tier A's finders raised from two to six, the file's size left alone — the edit a
    bytecode cache cannot tell from the file it already compiled."""
    widened = GOOD_TIERS.replace(
        '{"finders": 2, "refuters": 2, "claim": 1, "effort": "high"}',
        '{"finders": 6, "refuters": 2, "claim": 1, "effort": "high"}',
    )
    if len(widened) != len(GOOD_TIERS):
        raise AssertionError("这次变异改动了文件长度，就不再是缓存能骗过的那一种了")
    return widened


def _widened_once() -> str:
    """`tiers.py` with tier A's finders raised from two to six — and both expectations
    inside its own self-test moved to match, so that self-test still passes."""
    widened = GOOD_TIERS.replace(
        '{"finders": 2, "refuters": 2, "claim": 1, "effort": "high"}',
        '{"finders": 6, "refuters": 2, "claim": 1, "effort": "high"}',
    )
    widened = widened.replace(
        "check(\"单轮并发数不超过环境上限 4\", [max_in_flight(topology(l)) for l in \"ABCDEG\"],\n"
        "          [2, 2, 1, 1, 1, 0])",
        "check(\"单轮并发数不超过环境上限 4\", [max_in_flight(topology(l)) for l in \"ABCDEG\"],\n"
        "          [6, 2, 1, 1, 1, 0])",
    )
    widened = widened.replace(
        "[([1, 2], 3), ([2], 2), ([1], 2), ([1], 2), ([1], 2)]",
        "[([1, 6], 3), ([2], 2), ([1], 2), ([1], 2), ([1], 2)]",
    )
    if widened == GOOD_TIERS or "finders\": 6" not in widened:
        raise AssertionError("这次变异没改成：tiers.py 的拓扑表变了，先更新这里的替换串")
    return widened


if __name__ == "__main__":
    unittest.main()
