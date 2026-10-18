#!/usr/bin/env python3
"""test_guard_single_read.py — each guard reads a source file at most once.

WHY THIS EXISTS. Three guards re-read the tree they had already read:
`frontend_grade` read every component once for the import graph, again to grade
it, and a third time for the board's line count; `catalog-ratchet` walked each
catalog file and then read it again to see what it renders; the manual-anchor
check parsed each page and read it again for the line numbers in its
diagnostics. None of the answers changed, which is exactly why the waste can
come back unnoticed — it costs time, not correctness. So each case runs one
guard once over a fixture tree with `Path.read_text` counted per path, and
requires that no path was read twice. Put a duplicate read back and this goes
red.

The fixture for each guard is that guard's own: `catalog-ratchet` builds its
tree with `FIXTURE`, the manual-anchor check gets two pages that pass, and the
grader gets one standalone component and one that reaches the API. Each run
also asserts its own result (component count, anchor count, exit code), so a
guard that silently did nothing cannot pass by reading no files.

Run: python3 .claude/scripts/test_guard_single_read.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def load(name: str) -> Any:
    """Load a guard by file name — `catalog-ratchet.py` is not importable."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location(
        "guard_" + Path(name).stem.replace("-", "_"), HERE / name
    )
    if spec is None or spec.loader is None:  # pragma: no cover - missing file
        raise ImportError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` looks its class's module up in `sys.modules`; registering
    # first is what `load_frontend_grade` in the ratchets does too.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


class CountReads:
    """`Path.read_text`, counting how often each resolved path is read.

    Restores the method on exit, so one case's counts do not leak into the next.
    """

    def __init__(self) -> None:
        self.counts: dict[Path, int] = {}
        self._original: Any = None

    def __enter__(self) -> CountReads:
        self._original = Path.read_text
        original = self._original
        counts = self.counts

        def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
            key = path.resolve()
            counts[key] = counts.get(key, 0) + 1
            return original(path, *args, **kwargs)

        Path.read_text = read_text  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        Path.read_text = self._original  # type: ignore[method-assign]

    def repeated(self) -> dict[Path, int]:
        """The paths read more than once: the failure this test is about."""
        return {path: count for path, count in self.counts.items() if count > 1}


class EachPathReadOnce(unittest.TestCase):
    def test_frontend_grade_reads_each_component_once(self) -> None:
        grade = load("frontend_grade.py")
        with tempfile.TemporaryDirectory(prefix="guard-single-read-grade-") as raw:
            root = Path(raw)
            write(
                root,
                {
                    "frontend/src/api.ts": (
                        "export const api = { get: () => fetch('/x') }\n"
                    ),
                    "frontend/src/components/Plain.vue": (
                        '<script setup lang="ts">\n'
                        "defineProps<{ n: number }>()\n"
                        "</script>\n<template><div>{{ n }}</div></template>\n"
                    ),
                    "frontend/src/components/Fetches.vue": (
                        "<script setup lang=\"ts\">\n"
                        "import { api } from '@/api'\n"
                        "api.get()\n"
                        "</script>\n<template><div /></template>\n"
                    ),
                },
            )
            with CountReads() as counter:
                result = grade.grade_frontend(root)
        self.assertEqual(result["components"], 2)
        self.assertEqual(result["grades"], {"A": 1, "B": 0, "C": 1, "D": 0})
        self.assertEqual(counter.repeated(), {})

    def test_catalog_ratchet_reads_each_file_once(self) -> None:
        ratchet = load("catalog-ratchet.py")
        with tempfile.TemporaryDirectory(prefix="guard-single-read-catalog-") as raw:
            root = Path(raw)
            ratchet._fixture(root)
            baseline = ratchet._fixture_baseline(root)
            with CountReads() as counter:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = ratchet.run(
                        root, baseline, update=False, init=False, listing=False
                    )
        self.assertEqual(code, 0)
        self.assertEqual(counter.repeated(), {})

    def test_manual_anchors_reads_each_page_once(self) -> None:
        anchors = load("check-manual-anchors.py")
        with tempfile.TemporaryDirectory(prefix="guard-single-read-manual-") as raw:
            manual = Path(raw)
            write(
                manual,
                {
                    "quickstart.md": (
                        "# 快速开始 {#top}\n\n连接你的仓库。\n\n"
                        "## 连接仓库 {#connect-repo}\n\n这一节讲如何连接。\n"
                    ),
                    "concepts.md": "# 概念 {#top}\n\n几个需要知道的词。\n",
                },
            )
            with CountReads() as counter:
                payload, problems = anchors.build(manual)
        self.assertEqual(problems, [])
        self.assertEqual(len(payload["anchors"]), 3)
        self.assertEqual(counter.repeated(), {})


if __name__ == "__main__":
    unittest.main()
