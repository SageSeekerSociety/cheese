"""Space copy talks about members, not about a course.

A space is the generic layer: the same copy serves course spaces, team spaces
and everything else. Its roles are 管理员 and 成员, never 教师 and 学生. The
English side already reads "Removed — it is gone from the members' list"; the
zh-CN side still said 学生 in the materials library.

This guard scans message *values* — never keys, a key may name the old concept
without anyone reading it — under
frontend/src/i18n/messages/zh-CN/spaces, and fails on any value saying
课程/教师/学生 that BASELINE does not already hold. The one teaching.json
value is course configuration explaining itself, so it stays.

The comparison is by file and value text, one-directional: a listed value may
disappear (or the whole file may shrink) without touching this file, but no
file may ever gain a value that says a course word. Keying on the text rather
than on a per-file count is what stops one course word being reworded away in
teaching.json while a new one takes its place in the same file: the new text
is a key the baseline does not hold, so it fails.
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

#: (message file relative to ZH_CN, value text) that still says a course word,
#: as the tree reads today. Only decreases are allowed.
BASELINE: Counter[tuple[str, str]] = Counter(
    {
        (
            "spaces/teaching.json",
            "这段话是给 AI 看的，学生看不到原文。可以写 "
            "{'{current_week}'}、{'{allowed_topics}'}、{'{avoid_in_code}'}，"
            "发送时替换成下面填写的内容。留空 = 这条指导不加任何要求。",
        ): 1,
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
    """Every value under spaces/ that says a course word, by file and text."""
    found: Counter[tuple[str, str]] = Counter()
    for path in sorted((root / "spaces").rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        for value in _values(json.loads(path.read_text(encoding="utf-8"))):
            if any(word in value for word in COURSE_WORDS):
                found[(relative, value)] += 1
    return found


def violations(
    found: Counter[tuple[str, str]],
    baseline: Counter[tuple[str, str]] = BASELINE,
) -> list[str]:
    """Every value saying a course word that *baseline* does not hold."""
    return [
        f"{path}: {count} new value(s) say 课程/教师/学生: {value!r}"
        for (path, value), count in sorted((found - baseline).items())
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

    def test_a_nested_value_saying_student_is_caught(self):
        # Real values sit one level down (materials.json -> toast.removed), so
        # the fixture does too: a scan that stopped at the top level would stay
        # green on the tree this guard has to fail.
        said = "已移除，学生那边的清单里没有了"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                json.dumps({"toast": {"removed": said}}, ensure_ascii=False),
                encoding="utf-8",
            )
            found = occurrences(root)
            self.assertEqual(found, Counter({("spaces/a.json", said): 1}))
            self.assertEqual(len(violations(found, Counter())), 1)

    def test_each_course_word_is_caught(self):
        # Literal, not COURSE_WORDS: a test that reads the constant it checks
        # shrinks with it and never says the constant lost a word.
        self.assertEqual(set(COURSE_WORDS), {"课程", "教师", "学生"})
        for word in ("课程", "教师", "学生"):
            with self.subTest(word=word), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "spaces").mkdir()
                (root / "spaces/a.json").write_text(
                    json.dumps({"x": f"给{word}的说明"}, ensure_ascii=False),
                    encoding="utf-8",
                )
                self.assertEqual(len(violations(occurrences(root), Counter())), 1)

    def test_a_message_file_in_a_subdirectory_is_scanned(self):
        # zh-CN/spaces/index/index.json exists, so the scan has to recurse.
        said = "学生名单"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces/sub").mkdir(parents=True)
            (root / "spaces/sub/a.json").write_text(
                json.dumps({"x": said}, ensure_ascii=False), encoding="utf-8"
            )
            self.assertEqual(
                occurrences(root), Counter({("spaces/sub/a.json", said): 1})
            )

    def test_a_value_saying_member_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                '{"removed": "已移除，成员那边的清单里没有了"}', encoding="utf-8"
            )
            self.assertEqual(occurrences(root), Counter())

    def test_a_key_naming_student_is_not_copy(self):
        # Both spellings: the old key analytics.json still carries, and a
        # Chinese key a regression that scanned keys would catch.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                '{"student": "Member", "学生": "Member"}', encoding="utf-8"
            )
            self.assertEqual(occurrences(root), Counter())

    def test_one_file_fixing_does_not_pay_for_another_gaining(self):
        listed = "已移除，学生那边的清单里没有了"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text('{"x": "成员"}', encoding="utf-8")
            (root / "spaces/b.json").write_text('{"x": "学生名单"}', encoding="utf-8")
            lines = violations(
                occurrences(root), Counter({("spaces/a.json", listed): 1})
            )
            self.assertEqual(len(lines), 1)
            self.assertIn("spaces/b.json", lines[0])

    def test_rewording_within_a_listed_file_is_caught(self):
        # The baseline names the text, not a per-file count: replacing it with
        # another course word in the same file is a new value, not a swap.
        listed = "学生看不到原文"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                '{"x": "使用者看不到原文", "y": "学生名单"}', encoding="utf-8"
            )
            lines = violations(
                occurrences(root), Counter({("spaces/a.json", listed): 1})
            )
            self.assertEqual(len(lines), 1)
            self.assertIn("学生名单", lines[0])

    def test_a_count_below_its_baseline_is_clean(self):
        # The ratchet is one-directional: one copy where the baseline lists
        # two is a decrease, not a change to report.
        said = "学生那边的清单"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                json.dumps({"x": said}, ensure_ascii=False), encoding="utf-8"
            )
            baseline = Counter({("spaces/a.json", said): 2})
            self.assertEqual(violations(occurrences(root), baseline), [])

    def test_a_second_copy_of_a_listed_value_is_a_new_occurrence(self):
        said = "学生那边的清单"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spaces").mkdir()
            (root / "spaces/a.json").write_text(
                json.dumps({"x": said, "y": said}, ensure_ascii=False),
                encoding="utf-8",
            )
            lines = violations(occurrences(root), Counter({("spaces/a.json", said): 1}))
            self.assertEqual(len(lines), 1)
            self.assertIn("1 new value(s)", lines[0])


if __name__ == "__main__":
    unittest.main()
