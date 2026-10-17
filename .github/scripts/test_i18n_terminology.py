"""The retired interface word 房间 must not come back into the copy.

docs/design-system.md §8.10 fixes one word per concept: the place people talk
in is 频道, never 房间/话题/全局/项目总览. docs/i18n-glossary.md says the same
for the English side. Nothing enforced it, so nine zh-CN message values still
said 房间 in the same tree that says 频道 299 times.

This guard scans message *values* — never keys, a key may name the old concept
without anyone reading it — under frontend/src/i18n/messages/zh-CN, and freezes
today's occurrences per file in BASELINE. A count may only fall: a listed file
that gains one, or any unlisted file that contains one, fails.

The English directory is not scanned: plenty of `{room}` strings there are
placeholder names rather than display words, so it needs a rule of its own.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZH_CN = ROOT / "frontend/src/i18n/messages/zh-CN"
RETIRED = "房间"

#: Values that still contain 房间, per message file, as of this guard's
#: introduction. Not a licence: each count may fall, never rise.
BASELINE = {
    "admin.json": 1,
    "apiError/account.json": 1,
    "ask.json": 1,
    "catalog.json": 1,
    "feedback/dashboard.json": 1,
    "global.json": 1,
    "notifications/ROOM_NOTICE.json": 1,
    "roomNotice/routine.json": 1,
}


def _values(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _values(value)


def occurrences(root: Path = ZH_CN) -> dict[str, int]:
    """Message files under *root* whose values say the retired word, and how many."""
    found: dict[str, int] = {}
    for path in sorted(root.rglob("*.json")):
        values = json.loads(path.read_text(encoding="utf-8"))
        hits = sum(1 for value in _values(values) if RETIRED in value)
        if hits:
            found[path.relative_to(root).as_posix()] = hits
    return found


class TerminologyGuardTest(unittest.TestCase):
    def test_no_message_gains_the_retired_word(self):
        found = occurrences()
        for path, count in sorted(found.items()):
            with self.subTest(path=path):
                self.assertLessEqual(
                    count,
                    BASELINE.get(path, 0),
                    f"{path} has {count} value(s) saying 「房间」, "
                    f"at most {BASELINE.get(path, 0)} allowed. The interface "
                    "word is 「频道」 (docs/design-system.md §8.10).",
                )

    def test_a_value_saying_the_retired_word_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(
                '{"x": "只有坐在这个房间里的 AI 队友可以提问"}', encoding="utf-8"
            )
            self.assertEqual(occurrences(root), {"a.json": 1})

    def test_a_value_saying_the_live_word_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(
                '{"x": "只有坐在这个频道里的 AI 队友可以提问"}', encoding="utf-8"
            )
            self.assertEqual(occurrences(root), {})

    def test_a_key_naming_the_retired_word_is_not_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text('{"房间": "频道"}', encoding="utf-8")
            self.assertEqual(occurrences(root), {})


if __name__ == "__main__":
    unittest.main()
