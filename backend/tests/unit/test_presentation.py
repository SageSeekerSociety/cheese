"""看板的列和卡面短语，全部从已有事实算出来 —— 表驱动，一格一例。

写成表而不是一串 assert，是因为这层的 bug 从来不是「某一条算错了」，而是「某两条
的先后反了」和「某一格没人想到」。一张表让缺的那一格是可数的。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.review.models import AcceptStatus
from app.domain.review.notes import NoteCode
from app.domain.room_task.models import TaskStatus
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
        "last_signal_at": None,
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
    base = {"note_code": None, "merge_state_word": None, "merge_who": None}
    return CardFacts(status=status, **{**base, **kw})


# —— 一条活的每一格 ——————————————————————————————————————————————

TASK_CASES = [
    # (名字, 事实, 列, 短语)
    ("闲着", task(), Column.building, "空闲"),
    # 一条活由房间会话里的一个分身做，所以「它还在不在」有两个答案，先问屏幕。
    (
        "分身在做，刚说过话",
        task(has_worker=True, last_signal_at=JUST_NOW),
        Column.building,
        "运行中",
    ),
    # 屏幕没了，那个分身一定也没了 —— 它住在房间的会话里，而它不会来说一声。
    (
        "分身所在的屏幕没了",
        task(has_worker=True, last_signal_at=JUST_NOW, room_screen_live=False),
        Column.building,
        "失联",
    ),
    (
        "屏幕还在，但分身早就没动静了",
        task(has_worker=True, last_signal_at=LONG_AGO),
        Column.building,
        "失联",
    ),
    # 干完了在等房间收卡，不是断了 —— 这一条安静得理直气壮。
    (
        "分身交了结论，等房间收卡",
        task(has_worker=True, last_signal_at=LONG_AGO, has_conclusion=True),
        Column.building,
        "空闲",
    ),
    # 同样安静得理直气壮的另一种：卡已经递出去了，在等人。分身干完活不会把
    # `subagent_id` 抹掉，所以「失联」要是抢在卡前面说，每一条等验收的活都会
    # 被误报成失联。
    (
        "分身递了卡，安静地等人验收",
        task(has_worker=True, last_signal_at=LONG_AGO, card=card(AcceptStatus.pending)),
        Column.needs_you,
        "等待验收",
    ),
    (
        "分身递了卡，检查还在跑",
        task(
            has_worker=True,
            last_signal_at=LONG_AGO,
            room_screen_live=False,
            card=card(AcceptStatus.pending_gate),
        ),
        Column.delivering,
        "检查运行中",
    ),
    (
        "闸门在跑",
        task(card=card(AcceptStatus.pending_gate)),
        Column.delivering,
        "检查运行中",
    ),
    # 等采纳的卡按合并态镜像的「谁的活」分列 (#718)。
    (
        "CI 在跑",
        task(
            card=card(AcceptStatus.pending, merge_state_word="unstable", merge_who="ci")
        ),
        Column.delivering,
        "等待检查",
    ),
    # 同一个「CI 红了」，平台已经派芝士去修 → 平台在推，不该催人。
    (
        "芝士在修 CI",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.checks_failed)),
        Column.delivering,
        "修复检查",
    ),
    (
        "合并态说检查红了（还没叫芝士）",
        task(
            card=card(
                AcceptStatus.pending, merge_state_word="blocked", merge_who="agent"
            )
        ),
        Column.delivering,
        "修复检查",
    ),
    (
        "和 main 冲突，芝士来解",
        task(
            card=card(AcceptStatus.pending, merge_state_word="dirty", merge_who="agent")
        ),
        Column.delivering,
        "解决冲突",
    ),
    (
        "落后基线，平台在更新分支",
        task(
            card=card(
                AcceptStatus.pending, merge_state_word="behind", merge_who="platform"
            )
        ),
        Column.delivering,
        "平台更新分支",
    ),
    (
        "合并冲突，芝士在解",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.merge_conflict)),
        Column.delivering,
        "解决冲突",
    ),
    ("等人采纳", task(card=card(AcceptStatus.pending)), Column.needs_you, "等待验收"),
    (
        "绿了等人采纳",
        task(
            card=card(AcceptStatus.pending, merge_state_word="clean", merge_who="human")
        ),
        Column.needs_you,
        "等待验收",
    ),
    # 同一个「CI 红了」，但芝士推不上去修 —— PR 上的红是旧的，没人能清掉它。
    (
        "修不上去的 CI",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.repush_failed)),
        Column.needs_you,
        "检查未通过",
    ),
    (
        "分叉了推不动",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.repush_diverged)),
        Column.needs_you,
        "检查未通过",
    ),
    (
        "GitHub 拒绝合并",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.merge_refused)),
        Column.needs_you,
        "交付被退回",
    ),
    (
        "PR 被人关掉了",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.pr_closed_unmerged)),
        Column.needs_you,
        "交付被退回",
    ),
    (
        "采纳时冲突",
        task(card=card(AcceptStatus.conflict)),
        Column.needs_you,
        "交付被退回",
    ),
    # 芝士提出了待确认问题，本轮停在这里等回答。这是唯一一种会中断运行的。
    ("提问未回答", task(awaiting_answer=True), Column.needs_you, "待确认"),
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
    # 草稿和空闲要分开：一个从没开始的房间，和一个做完一轮在等下一句话的房间，
    # 对看的人不是一回事。
    ("草稿", room(status=TopicStatus.draft), Column.building, "草稿"),
    (
        "闸门在跑",
        room(card=card(AcceptStatus.pending_gate)),
        Column.delivering,
        "检查运行中",
    ),
    ("等人采纳", room(card=card(AcceptStatus.pending)), Column.needs_you, "等待验收"),
    ("提问未回答", room(awaiting_answer=True), Column.needs_you, "待确认"),
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
            has_worker=True,
            last_signal_at=JUST_NOW,
            card=card(AcceptStatus.pending),
        ),
        now=NOW,
    )
    assert (shown.column, shown.display_status) == (Column.done, "已采纳")


def test_the_live_fact_beats_the_paperwork():
    """在跑压过卡：卡描述的是它可能马上就要顶掉的那一版。"""
    shown = task_presentation(
        task(
            has_worker=True,
            last_signal_at=JUST_NOW,
            card=card(AcceptStatus.pending),
        ),
        now=NOW,
    )
    assert (shown.column, shown.display_status) == (Column.building, "运行中")


def test_an_unanswered_question_beats_the_live_fact():
    """提问压过「在跑」—— 规矩 2 唯一的例外，而且是同一个道理。

    进程可能还在，但「在跑」已经不是此刻成立的事实：它停在那个问题上等回答，不会
    自己往下走。而看板显示「运行中」，正是让人不来看的那一句。
    """
    shown = task_presentation(
        task(
            awaiting_answer=True,
            has_worker=True,
            last_signal_at=JUST_NOW,
            card=card(AcceptStatus.pending),
        ),
        now=NOW,
    )
    assert (shown.column, shown.display_status) == (Column.needs_you, "待确认")


def test_delivery_still_beats_an_unanswered_question():
    """已交付压过它 —— 已经采纳，那个旧问题不再挡住任何事。"""
    shown = task_presentation(
        task(awaiting_answer=True, accepted_at=JUST_NOW), now=NOW
    )
    assert (shown.column, shown.display_status) == (Column.done, "已采纳")


def test_a_room_with_an_unanswered_question_beats_running_too():
    shown = room_presentation(
        RoomFacts(
            status=TopicStatus.active,
            running=True,
            accepted_at=None,
            card=None,
            awaiting_answer=True,
        ),
        now=NOW,
    )
    assert (shown.column, shown.display_status) == (Column.needs_you, "待确认")


def test_an_archived_room_does_not_ask_anything_of_anyone():
    shown = room_presentation(
        RoomFacts(
            status=TopicStatus.archived,
            running=False,
            accepted_at=None,
            card=None,
            awaiting_answer=True,
        ),
        now=NOW,
    )
    assert shown.column == Column.archived


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


def test_every_phrase_is_reachable():
    """每一句话都得有活的/房间的事实能点亮它。

    这是「等待回答」被砍掉的那条规矩的另一半：一个契约里有、却永远不会出现的值，
    下一个人会以为它在工作、去查为什么从来不亮。所以词表里有几句，这里就得有
    几个例子——加词而不加事实，这条会红。
    """
    reached = {p for _, _, _, p in TASK_CASES} | {p for _, _, _, p in ROOM_CASES}
    from app.domain.room_task.presentation import COLUMN_PHRASES

    every = {p for phrases in COLUMN_PHRASES.values() for p in phrases}
    assert reached == every


def test_a_worker_that_stopped_reporting_is_not_a_worker_that_finished():
    """分身报完成不是活干完了 —— 一个分身可以报好几次完成（把长命令丢进自己的后台
    再停下来等也算一次），所以看板绝不能因为它安静下来就把这条活翻成「已收工」。
    只有收工（`TaskStatus.closed`）或者已交付才是终态。"""
    quiet = task(has_worker=True, last_signal_at=LONG_AGO)
    shown = task_presentation(quiet, now=NOW)
    assert shown.column is Column.building, "断了联系不等于干完了"
    assert shown.display_status == "失联"

    gone = task_presentation(
        task(has_worker=True, last_signal_at=JUST_NOW, room_screen_live=False), now=NOW
    )
    assert gone.column is Column.building


def test_a_finished_thread_still_reads_as_finished_with_a_worker_on_it():
    """反过来也得成立：绑过分身不能盖掉真正的终态。"""
    closed = task(has_worker=True, status=TaskStatus.closed, last_signal_at=LONG_AGO)
    assert task_presentation(closed, now=NOW).display_status == "已收工"
    delivered = task(has_worker=True, accepted_at=JUST_NOW, last_signal_at=LONG_AGO)
    assert task_presentation(delivered, now=NOW).display_status == "已采纳"


def test_a_thread_waiting_on_a_person_is_not_out_of_contact():
    """分身干完活不会把自己从这条活上摘掉，所以「等人」的每一格都要能压过失联 ——
    否则整个 delivering / needs_you 两列会被一句「失联」抹平。"""
    for status in (
        AcceptStatus.pending,
        AcceptStatus.pending_gate,
        AcceptStatus.conflict,
    ):
        quiet = task(
            has_worker=True,
            last_signal_at=LONG_AGO,
            room_screen_live=False,
            card=card(status),
        )
        shown = task_presentation(quiet, now=NOW)
        assert shown.display_status != "失联", f"{status} 的卡被失联抢答了"
        assert shown.column in (Column.delivering, Column.needs_you)


def test_it_reads_nothing_but_the_facts_it_was_given():
    """纯函数：同样的事实 + 同样的「现在几点」= 同样的答案，跑多少次都一样。"""
    facts = task(has_worker=True, last_signal_at=LONG_AGO)
    first = task_presentation(facts, now=NOW)
    assert first == task_presentation(facts, now=NOW)
    # 只有「现在几点」变了，同一行事实就换了一格 —— 时间是参数，不是它自己去读的。
    assert (
        task_presentation(facts, now=LONG_AGO + timedelta(minutes=1)).display_status
        == "运行中"
    )
