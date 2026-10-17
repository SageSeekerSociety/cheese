#!/usr/bin/env python3
"""test_check_i18n_lists.py — check-i18n-lists.py fires on the drift it describes.

Every case is a whole tree the checker is pointed at with `--root`, so the guard
is exercised the way it runs: same anchors, same line numbers in the messages.

Two of the cases are about *not* firing. The §2 tree also lists file namespaces
(`global.json`, `index.ts`) and the build comment also carries a `.json` token;
neither is a directory, and a parser that read either would fail on today's repo.

Run: python3 .claude/scripts/test_check_i18n_lists.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "check-i18n-lists.py"
REPO_ROOT = HERE.parents[1]

#: The directory set a fixture is built from. The real trees hold eight; five is
#: enough to exercise every branch, and keeps the fixture's prose short.
DIRS = ("account", "apiError", "roomNotice", "spaces", "work")


def tree_rows(dirs: tuple[str, ...]) -> list[str]:
    return [f"    {name}/           ← 见下「大词表按子树拆」" for name in dirs]


def big_split(dirs: tuple[str, ...]) -> str:
    head = "、".join(f"`{name}`（1 条）" for name in dirs[:-1])
    return f"{head}和 `{dirs[-1]}`（1 条）"


def cjk_hint(dirs: tuple[str, ...]) -> str:
    return "、".join(f"`{name}`" for name in dirs)


def build_list(dirs: tuple[str, ...]) -> str:
    head = ", ".join(f"`{name}/`" for name in dirs[:-1])
    return f"{head} and `{dirs[-1]}/`"


def doc_text(dirs: tuple[str, ...]) -> str:
    lines = [
        "# 中英双语与词条目录",
        "",
        "## 2. 目录与命名空间",
        "",
        "两个语言目录**结构镜像**，这是硬要求：",
        "",
        "```",
        "frontend/src/i18n/messages/",
        "  catalog.ts        ← 两份语言包共用的加载器",
        "  zh-CN/",
        "    index.ts        ← 一行 glob，不用登记表",
        "    global.json     ← 跨界面通用",
        "    navigation.json ← 导航项",
        *tree_rows(dirs),
        "  en/",
        "    index.ts",
        "    (同名文件，一一对应，键也一一对应)",
        "```",
        "",
        "**大词表按子树拆文件，键路径不变。**",
        big_split(dirs),
        "各自是一个命名空间，只是分别装在目录树里。",
        "",
        "## 5. 机器闸门",
        "",
        "把串放进 `messages/zh-CN/<键路径>.json` 和 `messages/en/<键路径>.json`，",
        f"键名见第 3 节（{cjk_hint(dirs)}是目录，见第 2 节）。",
    ]
    return "\n".join(lines) + "\n"


def build_text(dirs: tuple[str, ...]) -> str:
    return (
        "const UI_STRINGS = path.join(REPO, 'frontend/src/i18n/messages/zh-CN')\n"
        f"// The catalog is a tree of files — {build_list(dirs)} are\n"
        "// directories since the split (docs/i18n.md §2) — so every `.json`\n"
        "// under it is read, at any depth.\n"
    )


def make_tree(
    root: Path,
    *,
    dirs: tuple[str, ...] = DIRS,
    zh_only: tuple[str, ...] = (),
    en_only: tuple[str, ...] = (),
    doc: str | None = None,
    build: str | None = None,
) -> Path:
    """Write a locale pair plus the two prose files, in the shapes the repo uses."""
    for locale, extra in (("zh-CN", zh_only), ("en", en_only)):
        for name in (*dirs, *extra):
            where = root / "frontend/src/i18n/messages" / locale / name
            where.mkdir(parents=True, exist_ok=True)
    doc_file = root / "docs/i18n.md"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text(doc or doc_text(dirs), encoding="utf-8")
    build_file = root / "docs/site/build.mjs"
    build_file.parent.mkdir(parents=True, exist_ok=True)
    build_file.write_text(build or build_text(dirs), encoding="utf-8")
    return root


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
    )


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class Passes(Fixture):
    def test_the_repository_agrees_today(self) -> None:
        result = run(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_consistent_tree_passes(self) -> None:
        make_tree(self.root)
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_file_rows_and_the_dot_json_token_are_not_directories(self) -> None:
        make_tree(self.root)
        doc = (self.root / "docs/i18n.md").read_text(encoding="utf-8")
        build = (self.root / "docs/site/build.mjs").read_text(encoding="utf-8")
        self.assertIn("global.json", doc)
        self.assertIn("`.json`", build)
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)


class Fires(Fixture):
    def test_a_directory_in_one_locale_only_fails(self) -> None:
        make_tree(self.root, zh_only=("ghost",))
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ghost", result.stderr)
        self.assertIn("does not mirror", result.stderr)

    def test_a_directory_in_both_locales_fails_as_unlisted(self) -> None:
        make_tree(self.root, zh_only=("ghost",), en_only=("ghost",))
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ghost", result.stderr)
        self.assertIn("does not list", result.stderr)

    def test_a_row_missing_from_the_tree_fails(self) -> None:
        make_tree(self.root, doc=doc_text(tuple(d for d in DIRS if d != "roomNotice")))
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"docs/i18n\.md:\d+: .*`roomNotice`")

    def test_a_row_the_tree_does_not_have_fails(self) -> None:
        make_tree(self.root, doc=doc_text((*DIRS, "ghost")))
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"docs/i18n\.md:\d+: lists `ghost`")

    def test_a_name_missing_from_the_big_split_sentence_fails(self) -> None:
        # Only that sentence loses the name; the tree and the hint still list it.
        doc = doc_text(DIRS).replace("`roomNotice`（1 条）、", "")
        make_tree(self.root, doc=doc)
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(
            result.stderr, r"docs/i18n\.md:\d+: .*does not list `roomNotice`"
        )

    def test_a_name_missing_from_the_cjk_hint_fails(self) -> None:
        without = tuple(d for d in DIRS if d != "work")
        doc = doc_text(DIRS).replace(cjk_hint(DIRS), cjk_hint(without))
        make_tree(self.root, doc=doc)
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"docs/i18n\.md:\d+: .*`work`")

    def test_a_name_missing_from_the_build_comment_fails(self) -> None:
        without = tuple(d for d in DIRS if d != "apiError")
        make_tree(self.root, build=build_text(without))
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"docs/site/build\.mjs:\d+: .*`apiError`")

    def test_a_rewritten_sentence_is_unjudgeable_not_a_pass(self) -> None:
        doc = doc_text(DIRS).replace(
            "**大词表按子树拆文件，键路径不变。**", "大词表拆成目录了。"
        )
        make_tree(self.root, doc=doc)
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot judge", result.stderr)
        self.assertIn("docs/i18n.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
