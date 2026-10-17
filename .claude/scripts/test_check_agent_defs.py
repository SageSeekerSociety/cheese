#!/usr/bin/env python3
"""Prove the agent-definition guard fires, and that this repository passes it.

Two jobs, and they are different questions:

- `test_this_repository_passes` runs the guard against the tree it lives in. The guard
  cannot be trusted to judge the repository unless it is run against the repository, and
  this is the half that makes it a gate rather than a script.
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

    def test_a_missing_frontmatter_is_reported(self) -> None:
        self._reject_definition("check-runner.md", "Just a body.\n", "frontmatter")

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

    def test_a_gateway_model_in_the_model_field_is_reported(self) -> None:
        """The dispatched-to model must be named as a subagent_type, not in `model`."""
        self._reject_definition(
            "check-runner.md",
            GOOD_DEFINITION.replace("model: sonnet", "model: kimi-k3"),
            "subagent_type",
        )

    def test_a_reserved_model_filename_is_reported(self) -> None:
        self._reject_definition(
            "model-kimi-k3.md",
            GOOD_DEFINITION.replace("name: check-runner", "name: kimi-k3"),
            "model-",
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
    def _run(root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            check=False, capture_output=True, text=True,
        )


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
        "[([1, 2], 4), ([2], 3), ([1], 3), ([1], 3), ([1], 3)]",
        "[([1, 2, 6], 4), ([2], 3), ([1], 3), ([1], 3), ([1], 3)]",
    )
    if widened == GOOD_TIERS or "finders\": 6" not in widened:
        raise AssertionError("这次变异没改成：tiers.py 的拓扑表变了，先更新这里的替换串")
    return widened


if __name__ == "__main__":
    unittest.main()
