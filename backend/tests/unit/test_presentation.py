"""看板的列和卡面短语，全部从已有事实算出来 —— 表驱动，一格一例。

写成表而不是一串 assert，是因为这层的 bug 从来不是「某一条算错了」，而是「某两条
的先后反了」和「某一格没人想到」。一张表让缺的那一格是可数的。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.review.models import AcceptStatus
from app.domain.review.notes import NoteCode
from app.domain.room_task.models import Residency, TaskStatus
from app.domain.room_task.presentation import (
    LOST_SIGNAL_AFTER,
    CardFacts,
    Column,
    RoomFacts,
    TaskFacts,
    room_presentation,
    task_presentation,
)
from app.domain.topic.models import TopicStatus

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
JUST_NOW = NOW - timedelta(minutes=1)
LONG_AGO = NOW - LOST_SIGNAL_AFTER - timedelta(minutes=1)


def task(**kw) -> TaskFacts:
    """A thread that is doing nothing at all, plus whatever the case is about."""
    base = {
        "status": TaskStatus.open,
        "residency": Residency.idle,
        "queued_at": None,
        "last_turn_at": None,
        "accepted_at": None,
        "card": None,
    }
    return TaskFacts(**{**base, **kw})


def room(**kw) -> RoomFacts:
    base = {
        "status": TopicStatus.active,
        "running": False,
        "accepted_at": None,
        "card": None,
    }
    return RoomFacts(**{**base, **kw})


def card(status: AcceptStatus, **kw) -> CardFacts:
    base = {"note_code": None, "pr_merged_at": None}
    return CardFacts(status=status, **{**base, **kw})


# —— 一条活的每一格 ——————————————————————————————————————————————

TASK_CASES = [
    # (名字, 事实, 列, 短语)
    (
        "在跑",
        task(residency=Residency.running, last_turn_at=JUST_NOW),
        Column.building,
        "运行中",
    ),
    ("排队", task(queued_at=JUST_NOW), Column.building, "排队中"),
    ("闲着", task(), Column.building, "空闲"),
    # 说自己在跑、但上一次有人确认它还活着已经太久了。今天前端没有这一格：
    # taskRing 只看 residency，于是一条隧道断掉的活和一条真在跑的活长得一样。
    (
        "在跑但早就没动静了",
        task(residency=Residency.running, last_turn_at=LONG_AGO),
        Column.building,
        "失联",
    ),
    # residency 说 running，却从来没有过一次 last_turn_at —— 没有任何东西确认过
    # 这一轮开起来了，所以不能说它在跑。
    (
        "在跑但从没被确认过",
        task(residency=Residency.running, last_turn_at=None),
        Column.building,
        "失联",
    ),
    (
        "闸门在跑",
        task(card=card(AcceptStatus.pending_gate)),
        Column.delivering,
        "检查运行中",
    ),
    (
        "PR 开着等 CI",
        task(card=card(AcceptStatus.pr_open)),
        Column.delivering,
        "等待检查",
    ),
    # 同一个「CI 红了」，平台已经派芝士去修 → 平台在推，不该催人。
    (
        "芝士在修 CI",
        task(card=card(AcceptStatus.pr_open, note_code=NoteCode.checks_failed)),
        Column.delivering,
        "修复检查",
    ),
    (
        "合并冲突，芝士在解",
        task(card=card(AcceptStatus.pr_open, note_code=NoteCode.merge_conflict)),
        Column.delivering,
        "等待合并",
    ),
    (
        "PR 合了，等落地",
        task(card=card(AcceptStatus.pr_open, pr_merged_at=JUST_NOW)),
        Column.delivering,
        "等待合并",
    ),
    ("等人采纳", task(card=card(AcceptStatus.pending)), Column.needs_you, "等待验收"),
    # 同一个「CI 红了」，但芝士推不上去修 —— PR 上的红是旧的，没人能清掉它。
    (
        "修不上去的 CI",
        task(card=card(AcceptStatus.pr_open, note_code=NoteCode.repush_failed)),
        Column.needs_you,
        "检查未通过",
    ),
    (
        "分叉了推不动",
        task(card=card(AcceptStatus.pr_open, note_code=NoteCode.repush_diverged)),
        Column.needs_you,
        "检查未通过",
    ),
    (
        "GitHub 拒绝合并",
        task(card=card(AcceptStatus.pr_open, note_code=NoteCode.merge_refused)),
        Column.needs_you,
        "交付被退回",
    ),
    (
        "PR 被人关掉了",
        task(card=card(AcceptStatus.pr_open, note_code=NoteCode.pr_closed_unmerged)),
        Column.needs_you,
        "交付被退回",
    ),
    (
        "采纳时冲突",
        task(card=card(AcceptStatus.conflict)),
        Column.needs_you,
        "交付被退回",
    ),
    ("已交付", task(accepted_at=JUST_NOW), Column.done, "已采纳"),
    ("收工了", task(status=TaskStatus.closed), Column.done, "已收工"),
]


@pytest.mark.parametrize(
    ("facts", "column", "phrase"),
    [(f, c, p) for _, f, c, p in TASK_CASES],
    ids=[name for name, *_ in TASK_CASES],
)
def test_a_thread_lands_in_one_column_with_one_phrase(facts, column, phrase):
    shown = task_presentation(facts, now=NOW)
    assert shown.column == column
    assert shown.display_status == phrase


# —— 一个房间的每一格 ————————————————————————————————————————————

ROOM_CASES = [
    ("在跑", room(running=True), Column.building, "运行中"),
    ("闲着", room(), Column.building, "空闲"),
    # 房间没有「草稿」这一格可用的短语，落回空闲 —— 一个还没开工的房间确实是空闲的。
    ("草稿", room(status=TopicStatus.draft), Column.building, "空闲"),
    (
        "闸门在跑",
        room(card=card(AcceptStatus.pending_gate)),
        Column.delivering,
        "检查运行中",
    ),
    ("等人采纳", room(card=card(AcceptStatus.pending)), Column.needs_you, "等待验收"),
    ("已交付", room(accepted_at=JUST_NOW), Column.done, "已采纳"),
    ("归档了", room(status=TopicStatus.archived), Column.archived, "已归档"),
]


@pytest.mark.parametrize(
    ("facts", "column", "phrase"),
    [(f, c, p) for _, f, c, p in ROOM_CASES],
    ids=[name for name, *_ in ROOM_CASES],
)
def test_a_room_lands_in_one_column_with_one_phrase(facts, column, phrase):
    shown = room_presentation(facts, now=NOW)
    assert shown.column == column
    assert shown.display_status == phrase


def test_every_column_is_reachable():
    """一列都不能是空的 —— 一个谁都到不了的列，在界面上是一块永远不亮的地方。"""
    reached = {c for _, _, c, _ in TASK_CASES} | {c for _, _, c, _ in ROOM_CASES}
    assert reached == set(Column)


# —— 两条优先级规矩 ——————————————————————————————————————————————


def test_delivery_beats_everything():
    """已交付压过一切：交付和 open/closed 不是同一个问题，一条活可以已交付还开着。"""
    shown = task_presentation(
        task(
            accepted_at=JUST_NOW,
            residency=Residency.running,
            last_turn_at=JUST_NOW,
            card=card(AcceptStatus.pending),
        ),
        now=NOW,
    )
    assert (shown.column, shown.display_status) == (Column.done, "已采纳")


def test_the_live_fact_beats_the_paperwork():
    """在跑压过卡：卡描述的是它可能马上就要顶掉的那一版。"""
    shown = task_presentation(
        task(
            residency=Residency.running,
            last_turn_at=JUST_NOW,
            card=card(AcceptStatus.pending),
        ),
        now=NOW,
    )
    assert (shown.column, shown.display_status) == (Column.building, "运行中")


# —— 结算掉的卡不再替这条活说话 ————————————————————————————————————

SETTLED = [
    AcceptStatus.accepted,
    AcceptStatus.rejected,
    AcceptStatus.revoked,
    AcceptStatus.gate_failed,
    AcceptStatus.gate_blocked,
]


@pytest.mark.parametrize("status", SETTLED, ids=[str(s) for s in SETTLED])
def test_a_settled_card_stops_answering(status):
    """一张已经结算的卡不是这条活此刻的状态 —— 它被驳回之后，活回到施工中。"""
    shown = task_presentation(task(card=card(status)), now=NOW)
    assert (shown.column, shown.display_status) == (Column.building, "空闲")


# —— 列 × 短语 的约束 ————————————————————————————————————————————


def test_no_phrase_can_appear_under_a_column_it_does_not_belong_to():
    """每一列只能产出属于自己的短语。

    这不是靠注释守的：列是从短语**推出来**的，没有任何一处代码分别挑一个列和一个
    短语，所以「显示了一句不属于本列的话」在结构上没有发生的余地。这条测试把那个
    结构钉住 —— 有人把列改成独立参数时它会红。
    """
    from app.domain.room_task.presentation import COLUMN_PHRASES

    seen: dict[str, Column] = {}
    for column, phrases in COLUMN_PHRASES.items():
        for phrase in phrases:
            assert phrase not in seen, (
                f"{phrase} 同时属于 {seen.get(phrase)} 和 {column}"
            )
            seen[phrase] = column

    for _, facts, column, phrase in TASK_CASES:
        assert phrase in COLUMN_PHRASES[column]
        assert (
            task_presentation(facts, now=NOW).display_status in COLUMN_PHRASES[column]
        )
    for _, facts, column, phrase in ROOM_CASES:
        assert phrase in COLUMN_PHRASES[column]
        assert (
            room_presentation(facts, now=NOW).display_status in COLUMN_PHRASES[column]
        )


def test_it_reads_nothing_but_the_facts_it_was_given():
    """纯函数：同样的事实 + 同样的「现在几点」= 同样的答案，跑多少次都一样。"""
    facts = task(residency=Residency.running, last_turn_at=LONG_AGO)
    first = task_presentation(facts, now=NOW)
    assert first == task_presentation(facts, now=NOW)
    # 只有「现在几点」变了，同一行事实就换了一格 —— 时间是参数，不是它自己去读的。
    assert (
        task_presentation(facts, now=LONG_AGO + timedelta(minutes=1)).display_status
        == "运行中"
    )
