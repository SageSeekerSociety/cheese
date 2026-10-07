"""看板的列和卡面短语，全部从已有事实算出来 —— 表驱动，一格一例。

写成表而不是一串 assert，是因为这层的 bug 从来不是「某一条算错了」，而是「某两条
的先后反了」和「某一格没人想到」。一张表让缺的那一格是可数的。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.review.models import AcceptStatus
from app.domain.review.notes import NoteCode
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.presentation import (
    Archived,
    Building,
    CardFacts,
    Column,
    Delivering,
    Done,
    NeedsYou,
    NotStarted,
    RoomFacts,
    TaskFacts,
    room_presentation,
    task_presentation,
)
from app.domain.topic.models import TopicStatus

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
JUST_NOW = NOW - timedelta(minutes=1)


def task(**kw) -> TaskFacts:
    """A started task doing nothing at all, plus whatever the case is about."""
    base = {
        "status": TaskStatus.open,
        "accepted_at": None,
        "card": None,
        "started": True,
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
    base = {
        "note_code": None,
        "merge_state_word": None,
        "merge_who": None,
        "decided": False,
    }
    return CardFacts(status=status, **{**base, **kw})


# —— 一条活的每一格 ——————————————————————————————————————————————

TASK_CASES = [
    # (名字, 事实, 列, 短语)
    # 负责人还没点「开始」：还在讨论，芝士只聊、只写文档。
    ("还在讨论", task(started=False), Column.not_started, NotStarted.discussing),
    # 讨论时芝士在回话，也还是讨论：「运行中」说的是它在改项目。
    (
        "讨论中，芝士正在回话",
        task(started=False, running=True),
        Column.not_started,
        NotStarted.discussing,
    ),
    # 讨论中芝士问了一个问题，下一步在人手上。
    (
        "讨论中，芝士在等回答",
        task(started=False, awaiting_answer=True),
        Column.needs_you,
        NeedsYou.awaiting_answer,
    ),
    ("开始了，在跑", task(running=True), Column.building, Building.running),
    # 开始了，此刻没有在跑的一轮，也还没递出改动。
    ("开始了，停着", task(), Column.building, Building.started),
    # 递出去的那一版被退回，而没有一轮在改：停在人手上。
    (
        "改动被退回",
        task(card=card(AcceptStatus.rejected)),
        Column.needs_you,
        NeedsYou.bounced,
    ),
    (
        "闸门在跑",
        task(card=card(AcceptStatus.pending_gate)),
        Column.delivering,
        Delivering.gate_running,
    ),
    # 等采纳的卡按合并态镜像的「谁的活」分列 (#718)。
    (
        "CI 在跑",
        task(
            card=card(AcceptStatus.pending, merge_state_word="unstable", merge_who="ci")
        ),
        Column.delivering,
        Delivering.awaiting_checks,
    ),
    # 同一个「CI 红了」，平台已经派芝士去修 → 平台在推，不该催人。
    (
        "芝士在修 CI",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.checks_failed)),
        Column.delivering,
        Delivering.fixing_checks,
    ),
    (
        "合并态说检查红了（还没叫芝士）",
        task(
            card=card(
                AcceptStatus.pending, merge_state_word="blocked", merge_who="agent"
            )
        ),
        Column.delivering,
        Delivering.fixing_checks,
    ),
    (
        "和 main 冲突，芝士来解",
        task(
            card=card(AcceptStatus.pending, merge_state_word="dirty", merge_who="agent")
        ),
        Column.delivering,
        Delivering.resolving_conflict,
    ),
    (
        "落后基线，平台在更新分支",
        task(
            card=card(
                AcceptStatus.pending, merge_state_word="behind", merge_who="platform"
            )
        ),
        Column.delivering,
        Delivering.updating_branch,
    ),
    (
        "合并冲突，芝士在解",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.merge_conflict)),
        Column.delivering,
        Delivering.resolving_conflict,
    ),
    # 采纳已经点过、PR 进了 GitHub 的合并队列：这个窗口里 GitHub 对合并态报的是
    # unknown，人会去读那张卡（读卡又会补一次快照），所以镜像随时可能被写成
    # unknown/平台 (#2046)。那一步在平台和队列手上，不是把球交回给人。
    (
        "已采纳，合并态还没算完",
        task(
            card=card(
                AcceptStatus.pending,
                merge_state_word="unknown",
                merge_who="platform",
                decided=True,
            )
        ),
        Column.delivering,
        Delivering.awaiting_checks,
    ),
    # 平台入队时亲手记下的那一笔比轮询读到的 unknown 硬：有它就不看镜像了。
    (
        "note 记着已进合并队列",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.waiting_merge_queue)),
        Column.delivering,
        Delivering.awaiting_checks,
    ),
    # 没采纳的卡读到 unknown 是另一回事：那是「还没算出来 / 读不出来」，采纳按钮
    # 还在人手上，不能因为读不出来就替他把球收走。
    (
        "没采纳，合并态读不出来",
        task(
            card=card(
                AcceptStatus.pending, merge_state_word="unknown", merge_who="platform"
            )
        ),
        Column.needs_you,
        NeedsYou.awaiting_review,
    ),
    (
        "等人采纳",
        task(card=card(AcceptStatus.pending)),
        Column.needs_you,
        NeedsYou.awaiting_review,
    ),
    (
        "绿了等人采纳",
        task(
            card=card(AcceptStatus.pending, merge_state_word="clean", merge_who="human")
        ),
        Column.needs_you,
        NeedsYou.awaiting_review,
    ),
    # 同一个「CI 红了」，但芝士推不上去修 —— PR 上的红是旧的，没人能清掉它。
    (
        "修不上去的 CI",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.repush_failed)),
        Column.needs_you,
        NeedsYou.checks_failed,
    ),
    (
        "分叉了推不动",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.repush_diverged)),
        Column.needs_you,
        NeedsYou.checks_failed,
    ),
    (
        "GitHub 拒绝合并",
        task(card=card(AcceptStatus.pending, note_code=NoteCode.merge_refused)),
        Column.needs_you,
        NeedsYou.bounced,
    ),
    (
        "采纳时冲突",
        task(card=card(AcceptStatus.conflict)),
        Column.needs_you,
        NeedsYou.bounced,
    ),
    # 芝士提出了待回答的问题，本轮停在这里等回答。这是唯一一种会中断运行的。
    (
        "提问未回答",
        task(awaiting_answer=True),
        Column.needs_you,
        NeedsYou.awaiting_answer,
    ),
    ("已交付", task(accepted_at=JUST_NOW), Column.done, Done.accepted),
    # 关闭时写了结论：做成了，产出不是一次合并（调研、讨论出的结论）。
    (
        "带着结论关闭",
        task(status=TaskStatus.closed, has_conclusion=True),
        Column.done,
        Done.completed,
    ),
    # 什么都没留下就关了：不做了。
    ("没留结论就关了", task(status=TaskStatus.closed), Column.done, Done.closed),
]


@pytest.mark.parametrize(
    ("facts", "column", "phrase"),
    [(f, c, p) for _, f, c, p in TASK_CASES],
    ids=[name for name, *_ in TASK_CASES],
)
def test_a_thread_lands_in_one_column_with_one_phrase(facts, column, phrase):
    shown = task_presentation(facts, now=NOW)
    assert shown.column == column
    assert shown.phrase == phrase


# —— 事实是从行上读出来的 ————————————————————————————————————————


def test_being_started_is_read_off_the_row():
    """「开始」从库里那一列读出来 —— 上面那张表测的是「给定事实算出哪一格」，它不会
    发现这一位压根没接上，那样一来每个任务都会停在「讨论中」。"""
    from app.domain.room_task.models import Task
    from app.domain.room_task.presentation import facts_for_task

    assert facts_for_task(Task()).started is False
    assert facts_for_task(Task(started_at=JUST_NOW)).started is True
    assert facts_for_task(Task(conclusion="查清了")).has_conclusion is True


# —— 一个房间的每一格 ————————————————————————————————————————————

ROOM_CASES = [
    ("在跑", room(running=True), Column.building, Building.running),
    ("闲着", room(), Column.building, Building.idle),
    # 草稿和空闲要分开：一个从没开始的房间，和一个做完一轮在等下一句话的房间，
    # 对看的人不是一回事。
    ("草稿", room(status=TopicStatus.draft), Column.building, Building.draft),
    (
        "闸门在跑",
        room(card=card(AcceptStatus.pending_gate)),
        Column.delivering,
        Delivering.gate_running,
    ),
    (
        "等人采纳",
        room(card=card(AcceptStatus.pending)),
        Column.needs_you,
        NeedsYou.awaiting_review,
    ),
    (
        "提问未回答",
        room(awaiting_answer=True),
        Column.needs_you,
        NeedsYou.awaiting_answer,
    ),
    ("已交付", room(accepted_at=JUST_NOW), Column.done, Done.accepted),
    ("归档了", room(status=TopicStatus.archived), Column.archived, Archived.archived),
]


@pytest.mark.parametrize(
    ("facts", "column", "phrase"),
    [(f, c, p) for _, f, c, p in ROOM_CASES],
    ids=[name for name, *_ in ROOM_CASES],
)
def test_a_room_lands_in_one_column_with_one_phrase(facts, column, phrase):
    shown = room_presentation(facts, now=NOW)
    assert shown.column == column
    assert shown.phrase == phrase


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
            running=True,
            status=TaskStatus.closed,
            card=card(AcceptStatus.pending),
        ),
        now=NOW,
    )
    assert (shown.column, shown.phrase) == (Column.done, Done.accepted)


def test_the_live_fact_beats_the_paperwork():
    """在跑压过卡：卡描述的是它可能马上就要顶掉的那一版。"""
    shown = task_presentation(
        task(running=True, card=card(AcceptStatus.pending)),
        now=NOW,
    )
    assert (shown.column, shown.phrase) == (Column.building, Building.running)


def test_an_unanswered_question_beats_the_live_fact():
    """提问压过「在跑」—— 规矩 2 唯一的例外，而且是同一个道理。

    进程可能还在，但「在跑」已经不是此刻成立的事实：它停在那个问题上等回答，不会
    自己往下走。而看板显示「运行中」，正是让人不来看的那一句。
    """
    shown = task_presentation(
        task(awaiting_answer=True, running=True, card=card(AcceptStatus.pending)),
        now=NOW,
    )
    assert (shown.column, shown.phrase) == (
        Column.needs_you,
        NeedsYou.awaiting_answer,
    )


def test_delivery_still_beats_an_unanswered_question():
    """已交付压过它 —— 已经采纳，那个旧问题不再挡住任何事。"""
    shown = task_presentation(task(awaiting_answer=True, accepted_at=JUST_NOW), now=NOW)
    assert (shown.column, shown.phrase) == (Column.done, Done.accepted)


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
    assert (shown.column, shown.phrase) == (
        Column.needs_you,
        NeedsYou.awaiting_answer,
    )


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

SETTLED = [AcceptStatus.accepted, AcceptStatus.revoked]

#: 结算了、但把球交回给芝士去改的那几种：退回、闸门判红、闸门没跑成。
BOUNCED = [AcceptStatus.rejected, AcceptStatus.gate_failed, AcceptStatus.gate_blocked]


@pytest.mark.parametrize("status", SETTLED, ids=[str(s) for s in SETTLED])
def test_a_settled_card_stops_answering(status):
    """一张已经结算的卡不是这个任务此刻的状态。"""
    shown = task_presentation(task(card=card(status)), now=NOW)
    assert (shown.column, shown.phrase) == (Column.building, Building.started)


@pytest.mark.parametrize("status", BOUNCED, ids=[str(s) for s in BOUNCED])
def test_a_returned_task_nobody_is_reworking_waits_on_a_person(status):
    """退回之后芝士在改，任务就在运行中；没有一轮在改、也没递新的，就停在人手上
    —— 不能还说「已开始」，好像有人在推。"""
    idle = task_presentation(task(card=card(status)), now=NOW)
    assert (idle.column, idle.phrase) == (Column.needs_you, NeedsYou.bounced)
    reworking = task_presentation(task(card=card(status), running=True), now=NOW)
    assert reworking.phrase == Building.running


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
        assert task_presentation(facts, now=NOW).phrase in COLUMN_PHRASES[column]
    for _, facts, column, phrase in ROOM_CASES:
        assert phrase in COLUMN_PHRASES[column]
        assert room_presentation(facts, now=NOW).phrase in COLUMN_PHRASES[column]


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


def test_a_task_not_started_shows_no_paperwork():
    """还没开始的任务没有改动可审：即使有一张旧卡挂着，它也还在讨论。"""
    shown = task_presentation(
        task(started=False, card=card(AcceptStatus.pending)), now=NOW
    )
    assert (shown.column, shown.phrase) == (Column.not_started, NotStarted.discussing)


def test_it_reads_nothing_but_the_facts_it_was_given():
    """纯函数：同样的事实 = 同样的答案，跑多少次都一样。"""
    facts = task(running=True)
    assert task_presentation(facts, now=NOW) == task_presentation(facts, now=NOW)


def test_a_narrow_rail_card_folds_into_the_same_facts_as_the_orm_row():
    """窄读入口交出来的 `RailCard` 和 ORM 那一行，读成同一份事实。

    路由不再把 ORM 行带出去，但它要的答案必须一模一样 —— 否则同一条活在侧栏和
    别处会说出两种话。每一位都要覆盖到：`note_code` 挑格子、`merge_state` 里的
    state/who 说这一步在谁手上、`decided_by`/`auto_merge_armed_by` 说这一步已经
    交出去了。`CardFacts` 是纯值，所以两边相等就是这一层的契约。
    """
    from app.domain.review.models import AcceptCard
    from app.domain.review.queries import RailCard
    from app.domain.room_task.presentation import facts_for_card

    def both(**spec) -> tuple[CardFacts | None, CardFacts | None]:
        shared = {
            "status": spec.get("status", AcceptStatus.pending),
            "pr_number": spec.get("pr_number"),
            "pr_url": spec.get("pr_url"),
            "note_code": spec.get("note_code"),
            "merge_state": spec.get("merge_state"),
            "decided_by": spec.get("decided_by"),
            "auto_merge_armed_by": spec.get("auto_merge_armed_by"),
        }
        card_id = uuid.uuid4()
        row = AcceptCard(
            id=card_id, topic_id=uuid.uuid4(), reviewer_handle="alice", **shared
        )
        return facts_for_card(row), facts_for_card(RailCard(id=card_id, **shared))

    specs = [
        {},
        {"note_code": NoteCode.waiting_merge_queue},
        {"merge_state": {"state": "behind", "who": "platform"}},
        {
            "merge_state": {"state": "unknown", "who": "platform"},
            "decided_by": "alice",
        },
        {
            "merge_state": {"state": "unknown", "who": "platform"},
            "auto_merge_armed_by": "alice",
        },
        {"status": AcceptStatus.accepted, "pr_number": 7, "pr_url": "u"},
    ]
    for spec in specs:
        from_orm, from_rail = both(**spec)
        assert from_rail is not None
        assert from_rail == from_orm

    # 不是「两边都算成 None」就等于：拿一位具体的事实对下来。
    _, rail_facts = both(
        merge_state={"state": "behind", "who": "platform"}, decided_by="alice"
    )
    assert rail_facts is not None
    assert rail_facts.status == "pending"
    assert rail_facts.merge_state_word == "behind"
    assert rail_facts.merge_who == "platform"
    assert rail_facts.decided is True
