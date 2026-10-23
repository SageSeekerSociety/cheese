"""The retired interface word 房间 must not come back into the copy.

docs/design-system.md §8.10 fixes one word per concept: the place people talk
in is 频道, never 房间/话题/全局/项目总览. docs/i18n-glossary.md says the same
for the English side. Nothing enforced it, so nine zh-CN message values still
said 房间 in the same tree that says 频道 299 times.

This guard scans message *values* — never keys, a key may name the old concept
without anyone reading it — under frontend/src/i18n/messages/zh-CN, and freezes
the values that still say 房间 in BASELINE. The comparison is exact, so fixing
one value and adding another in the same file cannot cancel out, a second copy
of a listed value is a new occurrence too, and a fixed value must have its
BASELINE line deleted in the same change. Nothing here ever loosens by itself.

The English directory is not scanned: plenty of `{room}` strings there are
placeholder names rather than display words, so it needs a rule of its own.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZH_CN = ROOT / "frontend/src/i18n/messages/zh-CN"
RETIRED = "房间"

#: (message file relative to ZH_CN, value text) as it reads today. Not a
#: count per file: see the module docstring for what that would let through.
BASELINE: Counter[tuple[str, str]] = Counter(
    {
        ("apiError/account.json", "登录后才能进入这个房间"): 1,
        ("ask.json", "撤回后这道题重新等回答，谁都能再选一次。你发的那句话还留在房间里。"): 1,
        (
            "catalog.json",
            "图片那一档：一张图铺在这一格里，图上能圈一块、写一句话、把合成好的那张图连同那句话交给房间。",
        ): 1,
        ("feedback/dashboard.json", "房间"): 1,
        ("global.json", "房间里任何地方都行"): 1,
        ("notifications/ROOM_NOTICE.json", "随房间归档"): 1,
    }
)


def _values(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _values(value)


def occurrences(root: Path = ZH_CN) -> Counter[tuple[str, str]]:
    """Every message value under *root* that says the retired word, exactly."""
    found: Counter[tuple[str, str]] = Counter()
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        for value in _values(json.loads(path.read_text(encoding="utf-8"))):
            if RETIRED in value:
                found[(relative, value)] += 1
    return found


def violations(
    found: Counter[tuple[str, str]], baseline: Counter[tuple[str, str]]
) -> list[str]:
    """Every way *found* differs from *baseline*, in both directions."""
    lines = []
    for (path, value), count in sorted((found - baseline).items()):
        lines.append(f"{path}: {count} new value(s) say 「房间」: {value!r}")
    for (path, value), count in sorted((baseline - found).items()):
        lines.append(
            f"{path}: {count} fixed value(s) stay listed in BASELINE; delete "
            f"the line for {value!r}"
        )
    return lines


class TerminologyGuardTest(unittest.TestCase):
    def test_no_message_gains_or_restores_the_retired_word(self):
        self.assertTrue(ZH_CN.is_dir(), f"no message tree at {ZH_CN}")
        lines = violations(occurrences(), BASELINE)
        self.assertEqual(
            lines,
            [],
            "The interface word is 「频道」 (docs/design-system.md §8.10).\n"
            + "\n".join(lines),
        )

    def test_a_value_saying_the_retired_word_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(
                '{"x": "只有坐在这个房间里的 AI 队友可以提问"}', encoding="utf-8"
            )
            found = occurrences(root)
            self.assertEqual(
                found, Counter({("a.json", "只有坐在这个房间里的 AI 队友可以提问"): 1})
            )
            self.assertEqual(len(violations(found, Counter())), 1)

    def test_a_value_saying_the_live_word_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(
                '{"x": "只有坐在这个频道里的 AI 队友可以提问"}', encoding="utf-8"
            )
            self.assertEqual(occurrences(root), Counter())

    def test_a_key_naming_the_retired_word_is_not_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text('{"房间": "频道"}', encoding="utf-8")
            self.assertEqual(occurrences(root), Counter())

    def test_a_second_copy_of_a_listed_value_is_a_new_occurrence(self):
        listed = "房间里任何地方都行"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(
                json.dumps({"one": listed, "two": listed}), encoding="utf-8"
            )
            found = occurrences(root)
            self.assertEqual(found, Counter({("a.json", listed): 2}))
            lines = violations(found, Counter({("a.json", listed): 1}))
            self.assertEqual(len(lines), 1)
            self.assertIn("1 new value(s)", lines[0])

    def test_fixing_a_listed_value_shrinks_the_baseline_in_the_same_change(self):
        listed = "房间里任何地方都行"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(
                '{"x": "频道里任何地方都行"}', encoding="utf-8"
            )
            lines = violations(occurrences(root), Counter({("a.json", listed): 1}))
            self.assertEqual(len(lines), 1)
            self.assertIn("delete the line", lines[0])


if __name__ == "__main__":
    unittest.main()
