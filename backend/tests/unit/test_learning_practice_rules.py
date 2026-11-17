"""巩固题并进判断与复习排期的两条规则，以及模型出的题怎样才算一组。

- 一组 3 道：全对升一档、错一道不变、错两道及以上降一档；到顶、到底就停在那里；
- 复习按 1 / 3 / 7 / 14 天：全对连对加一、错一道不变、错两道归零，连对四组不再提醒；
- 模型的回答里不足 3 道像样的题（有题干、选项不重复、答案是其中一个）就不算一组。
"""

import json
from datetime import date

import pytest

from app.domain.space.learning_practice_analysis import parse_questions
from app.domain.space.learning_practice_service import next_level, next_review

TODAY = date(2026, 11, 17)


@pytest.mark.parametrize(
    ("level", "correct", "expected"),
    [
        ("weak", 3, "shaky"),
        ("shaky", 3, "mastered"),
        ("mastered", 3, "mastered"),
        ("shaky", 2, "shaky"),
        ("mastered", 1, "shaky"),
        ("shaky", 0, "weak"),
        ("weak", 0, "weak"),
    ],
)
def test_a_set_moves_the_level_by_how_many_were_right(level, correct, expected):
    assert next_level(level, correct=correct, total=3) == expected


def test_reviews_space_out_while_the_sets_stay_right_and_restart_when_they_do_not():
    streak, due = next_review(0, correct=3, total=3, today=TODAY)
    assert (streak, due) == (1, date(2026, 11, 20))
    streak, due = next_review(streak, correct=3, total=3, today=TODAY)
    assert due == date(2026, 11, 24)
    # 错一道：间隔不变。
    assert next_review(streak, correct=2, total=3, today=TODAY) == (2, due)
    # 错两道：从一天后重新来。
    assert next_review(streak, correct=1, total=3, today=TODAY) == (
        0,
        date(2026, 11, 18),
    )


def test_four_right_sets_in_a_row_end_the_reminders():
    streak = 0
    due: date | None = TODAY
    for _ in range(4):
        streak, due = next_review(streak, correct=3, total=3, today=TODAY)
    assert due is None


def _question(**overrides) -> dict:
    return {
        "stem": "内层怎样引用外层那一行？",
        "options": ["用别名", "用 GROUP BY", "用 HAVING", "不能"],
        "answer": 0,
        "explanation": "用别名。",
        **overrides,
    }


def test_three_sound_questions_make_a_set_and_broken_ones_are_dropped():
    answer = json.dumps(
        {
            "questions": [
                _question(),
                _question(answer=9),  # 答案不在选项里
                _question(options=["一样", "一样"]),  # 选项重复
                _question(stem=""),
                _question(stem="第二道"),
                _question(stem="第三道"),
            ]
        }
    )
    questions = parse_questions(answer)
    assert questions is not None
    assert [q["stem"] for q in questions] == [
        "内层怎样引用外层那一行？",
        "第二道",
        "第三道",
    ]


def test_fewer_than_three_sound_questions_are_not_a_set():
    assert (
        parse_questions(json.dumps({"questions": [_question(), _question()]})) is None
    )
    assert parse_questions("没有 JSON") is None
