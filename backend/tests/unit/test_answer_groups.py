"""整组作答的那几步：解析答案、把答案记到题上、生成交上去的那段正文。

不碰数据库：`record_answers` 只改块的 `meta`，`summary_line` 只读它。
"""

import uuid

import pytest

from app.core.errors import ConflictError, ForbiddenError, ValidationError
from app.domain.block.answer_groups import (
    Answer,
    answers_from,
    record_answers,
    summary_line,
)
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType


def question(*, options=("按自然周", "按滚动 7 天"), asked=("alice",), **meta):
    return Block(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        author="cheese",
        author_type=AuthorType.participant,
        content="统计口径按哪个来？",
        kind=BlockKind.message,
        seq=1,
        refs=[],
        meta={
            "options": [{"text": o} for o in options],
            "asked": list(asked),
            "asked_all": False,
            "answer_log": [],
            **meta,
        },
        created_at="2026-10-22T00:00:00Z",
    )


def test_no_option_means_no_answer():
    assert Answer.parse(None).empty
    assert Answer.parse({}).empty
    # 空白字符串也不算「写了什么」。
    assert Answer.parse({"note": "   "}).empty


def test_options_are_trimmed_and_blank_ones_dropped():
    answer = Answer.parse({"options": [" A ", "", "B"]})
    assert answer.options == ("A", "B")


def test_answers_must_be_keyed_by_question_id():
    with pytest.raises(ValidationError):
        answers_from({"answers": {"not-a-uuid": {"options": ["A"]}}})
    with pytest.raises(ValidationError):
        answers_from({"answers": {}})
    with pytest.raises(ValidationError):
        answers_from({})


def test_record_writes_one_entry_per_answered_question():
    first, second = question(), question(asked=("alice",))
    answers = {
        first.id: Answer(options=("按自然周",)),
        second.id: Answer(note="按季度"),
    }

    touched = record_answers([first, second], answers, by="alice", at=_at())

    assert touched == [first, second]
    assert first.meta["answer_log"][-1] == {
        "kind": "option",
        "option": "按自然周",
        "note": None,
        "by": "alice",
        "at": "2026-10-22T00:00:00+00:00",
        "reply_id": None,
    }
    assert second.meta["answer_log"][-1]["kind"] == "note"
    assert second.meta["answer_log"][-1]["note"] == "按季度"


def test_multi_select_is_stored_as_one_joined_answer():
    multi = question(
        options=("已合并的 pull request", "issue 评论", "全部活动"), multi_select=True
    )
    answers = {multi.id: Answer(options=("issue 评论", "全部活动"))}

    record_answers([multi], answers, by="alice", at=_at())

    assert multi.meta["answer_log"][-1]["option"] == "issue 评论、全部活动"


def test_an_unanswered_question_keeps_waiting():
    first, second = question(), question()
    answers = {first.id: Answer(options=("按自然周",))}

    touched = record_answers([first, second], answers, by="alice", at=_at())

    assert touched == [first]
    assert second.meta["answer_log"] == []


def test_nothing_answered_is_refused():
    with pytest.raises(ValidationError):
        record_answers([question()], {}, by="alice", at=_at())


def test_only_someone_it_asks_can_answer():
    asked_of_bob = question(asked=("bob",))
    with pytest.raises(ForbiddenError):
        record_answers(
            [asked_of_bob],
            {asked_of_bob.id: Answer(options=("按自然周",))},
            by="alice",
            at=_at(),
        )


def test_another_persons_answer_cannot_be_overwritten():
    """别人答过的那道题交不进去——一道题只有一个回答。"""
    answered = question()
    answered.meta = {**answered.meta, "answer_log": [_entry("bob", "按自然周")]}

    with pytest.raises(ConflictError):
        record_answers(
            [answered],
            {answered.id: Answer(options=("按滚动 7 天",))},
            by="alice",
            at=_at(),
        )


def test_the_answerer_can_change_their_own_answer():
    """答过的人改得动自己那条：撤掉旧的、写上新的一条。"""
    answered = question()
    answered.meta = {**answered.meta, "answer_log": [_entry("alice", "按自然周")]}

    record_answers(
        [answered],
        {answered.id: Answer(options=("按滚动 7 天",))},
        by="alice",
        at=_at(),
    )

    log = answered.meta["answer_log"]
    assert [e["by"] for e in log] == ["alice"]
    assert log[0]["option"] == "按滚动 7 天"


def test_editing_leaves_another_persons_skip_alone():
    """跳过不算回答：改自己的答案时，别人那条跳过要被原样留着。"""
    answered = question(asked=("alice", "bob"))
    answered.meta = {
        **answered.meta,
        "answer_log": [
            {
                "kind": "skipped",
                "option": None,
                "note": None,
                "by": "bob",
                "at": "",
                "reply_id": None,
            },
            _entry("alice", "按自然周"),
        ],
    }

    record_answers(
        [answered],
        {answered.id: Answer(options=("按滚动 7 天",))},
        by="alice",
        at=_at(),
    )

    log = answered.meta["answer_log"]
    assert [e["kind"] for e in log] == ["skipped", "option"]
    assert log[1]["option"] == "按滚动 7 天"


def _entry(by: str, option: str) -> dict:
    return {
        "kind": "option",
        "option": option,
        "note": None,
        "by": by,
        "at": "",
        "reply_id": None,
    }


def test_summary_lists_every_question_including_the_unanswered():
    first = question(header="统计口径")
    second = question(asked=("alice",))

    line = summary_line([first, second], {first.id: Answer(options=("按自然周",))})

    assert line.splitlines() == [
        "已回答上面的 1 个问题",
        "- 统计口径：按自然周",
        "- 第 2 题：未作答",
    ]


def _at():
    from datetime import UTC, datetime

    return datetime(2026, 10, 22, tzinfo=UTC)
