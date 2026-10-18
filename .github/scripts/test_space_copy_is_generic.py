"""Space copy talks about members, not about a course.

A space is the generic layer: the same copy serves course spaces, team spaces
and everything else. Its roles are 管理员 and 成员, never 教师 and 学生. The
English side already reads "Removed — it is gone from the members' list"; the
zh-CN side still said 学生 in the materials library.

This guard scans message *values* — never keys, a key may name the old concept
without anyone reading it — under
frontend/src/i18n/messages/zh-CN/spaces, and fails when a file holds more
values saying 课程/教师/学生 than BASELINE allows. The one teaching.json value
is course configuration explaining itself, not space-level copy, so it stays.

The comparison is per file and one-directional: a file may drop below its
baseline (or leave the baseline entirely) without touching this file, but no
file may ever exceed it. Counting per file rather than in total keeps a fixed
value in one file from paying for a new one in another.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZH_CN = ROOT / "frontend/src/i18n/messages/zh-CN"
COURSE_WORDS = ("课程", "教师", "学生")

#: Values saying a course word that each message file may still hold, as it
#: reads today. Paths are relative to ZH_CN. Only decreases are allowed.
BASELINE: Counter[str] = Counter({"spaces/teaching.json": 1})


def _values(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _values(value)


def occurrences(root: Path = ZH_CN) -> Counter[str]:
    """Per message file, how many values under spaces/ say a course word."""
    found: Counter[str] = Counter()
    for path in sorted((root / "spaces").rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        for value in _values(json.loads(path.read_text(encoding="utf-8"))):
            if any(word in value for word in COURSE_WORDS):
                found[relative] += 1
    return found


def violations(found: Counter[str], baseline: Counter[str] = BASELINE) -> list[str]:
    """Every file holding more course words than *baseline* allows."""
    return [
        f"{path}: {count} value(s) say 课程/教师/学生, baseline allows "
        f"{baseline.get(path, 0)}"
        for path, count in sorted(found.items())
        if count > baseline.get(path, 0)
    ]


class SpaceCopyGuardTest(unittest.TestCase):
    def test_no_message_file_gains_course_words(self):
        self.assertTrue((ZH_CN / "spaces").is_dir(), f"no space copy at {ZH_CN}")
        lines = violations(occurrences())
        self.assertEqual(
            lines,
            [],
            "Spaces are generic: use 管理员/成员, not 教师/学生 "
            "(space-level-features-not-course-named).\n" + "\n".join(lines),
        )

    def test_a_value_saying_student_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                '{"removed": "已移除，学生那边的清单里没有了"}', encoding="utf-8"
            )
            found = occurrences(root)
            self.assertEqual(found, Counter({"spaces/a.json": 1}))
            self.assertEqual(len(violations(found, Counter())), 1)

    def test_a_value_saying_member_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                '{"removed": "已移除，成员那边的清单里没有了"}', encoding="utf-8"
            )
            self.assertEqual(occurrences(root), Counter())

    def test_a_key_naming_student_is_not_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                '{"student": "Member"}', encoding="utf-8"
            )
            self.assertEqual(occurrences(root), Counter())

    def test_one_file_fixing_does_not_pay_for_another_gaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text('{"x": "成员"}', encoding="utf-8")
            (root / "spaces/b.json").write_text('{"x": "学生"}', encoding="utf-8")
            lines = violations(occurrences(root), Counter({"spaces/a.json": 1}))
            self.assertEqual(len(lines), 1)
            self.assertIn("spaces/b.json", lines[0])

    def test_a_file_below_its_baseline_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text('{"x": "成员"}', encoding="utf-8")
            self.assertEqual(
                violations(occurrences(root), Counter({"spaces/a.json": 1})), []
            )


if __name__ == "__main__":
    unittest.main()
