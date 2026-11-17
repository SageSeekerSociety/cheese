"""每晚学情分析里不碰库与网关的那一半：读模型的回答、配芝士的回复、算「哪一晚」。

规则：

- 档位只认三个值；知识点只认清单里的名字（去空白、不分大小写）；
- 依据只能是这一批里成员自己发言的编号，一条都对不上的判断整条丢掉；
- 提问只认这一批里的编号，类型不认识的归「其他」；
- 回答里没有 JSON 对象就是「读不出来」（``None``，不推进游标），而不是「没有判断」；
- 一段芝士回复只跟在它前面那条成员发言后面，成员在同一处又说话之后就不再算；
- 「这一晚」按 Asia/Shanghai 的日历算。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.space import learning_insight_analysis as la
from app.domain.space.learning_insight_repositories import Said

AT = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
ROOM = uuid.uuid4()


def _said(i: int, *, room=ROOM, author="ann", minutes=None) -> Said:
    return Said(
        id=uuid.uuid4(),
        conversation_id=room,
        author=author,
        content=f"第 {i} 条",
        created_at=AT + timedelta(minutes=i if minutes is None else minutes),
    )


POINTS = [
    SimpleNamespace(id=1, name="相关子查询"),
    SimpleNamespace(id=2, name="GROUP BY"),
]


def _parse(payload, messages):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return la.parse(content, points=POINTS, messages=messages)  # type: ignore[arg-type]


def test_a_judgment_keeps_only_evidence_it_actually_read():
    messages = [_said(1), _said(2)]
    reading = _parse(
        {
            "judgments": [
                {
                    "point": " 相关子查询 ",
                    "level": "weak",
                    "summary": "你还不清楚",
                    "evidence": ["m1", "m7", "m2", "m1"],
                }
            ]
        },
        messages,
    )
    assert reading is not None
    (judged,) = reading.judgments
    assert judged.point_id == 1
    # m7 不在这一批：丢掉；重复的去掉；最新的那条在前。
    assert judged.evidence == [messages[1].id, messages[0].id]


def test_judgments_without_evidence_unknown_points_or_levels_are_dropped():
    messages = [_said(1)]
    reading = _parse(
        {
            "judgments": [
                {"point": "相关子查询", "level": "weak", "evidence": ["m9"]},
                {"point": "相关子查询", "level": "weak", "evidence": []},
                {"point": "不在清单里", "level": "weak", "evidence": ["m1"]},
                {"point": "GROUP BY", "level": "excellent", "evidence": ["m1"]},
                {"point": "group by", "level": "shaky", "evidence": ["m1"]},
            ]
        },
        messages,
    )
    assert reading is not None
    assert [(j.point_id, j.level) for j in reading.judgments] == [(2, "shaky")]


def test_questions_need_a_known_ref_and_an_unknown_kind_is_other():
    messages = [_said(1), _said(2)]
    reading = _parse(
        {
            "questions": [
                {"ref": "m1", "point": "相关子查询", "kind": "concept", "gist": "g"},
                {"ref": "m2", "point": "", "kind": "rant"},
                {"ref": "m5", "kind": "error"},
            ]
        },
        messages,
    )
    assert reading is not None
    assert [(q.block_id, q.point_id, q.kind) for q in reading.questions] == [
        (messages[0].id, 1, "concept"),
        (messages[1].id, None, "other"),
    ]


def test_an_answer_with_no_json_object_is_unreadable_not_empty():
    for content in ("", "我看不出来", "{broken", "[1, 2]"):
        assert _parse(content, [_said(1)]) is None
    empty = _parse('好的：{"judgments": [], "questions": []}', [_said(1)])
    assert empty is not None and empty.judgments == [] and empty.questions == []


def test_a_reply_belongs_to_the_message_it_follows_until_the_member_speaks_again():
    first, second = _said(1, minutes=0), _said(2, minutes=10)
    elsewhere = _said(3, room=uuid.uuid4(), minutes=0)
    replies = [
        _said(9, author="cheese", minutes=1),  # after first, before second
        _said(9, author="cheese", minutes=2),  # a second reply: not taken
        _said(9, author="cheese", minutes=11),  # after second
    ]
    got = la.replies_after([first, second, elsewhere], replies)
    assert got[first.id] is replies[0]
    assert got[second.id] is replies[2]
    assert elsewhere.id not in got  # a reply in another conversation is not its


def test_the_night_is_counted_on_the_shanghai_calendar():
    # 2026-11-16 18:30 UTC 是上海的 11-17 02:30。
    day, hour = la.local_day(datetime(2026, 11, 16, 18, 30, tzinfo=UTC))
    assert (day.isoformat(), hour) == ("2026-11-17", 2)
