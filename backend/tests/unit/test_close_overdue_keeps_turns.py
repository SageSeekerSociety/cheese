"""A task closed on work past its deadline keeps the turns that close stopped.

The bug this covers: ``unfinished`` read the open turns of a task and threw
away their ids, keeping only the prompt text. So when the platform closed
an overdue task and stopped what was still running in it, the room got one line
naming the prompts and nothing anywhere said which turns had been stopped: no
run record carried a turn id, and the turn log (which reads failure events by
`blocks.turn_id`) could not see the stop. The work was gone and the record of
it with the task.

What is pinned here is what survives the close: every stopped turn's id, kept
on the closing task (`Overdue.undelivered`), and one run record per stopped
turn, under that turn's own number. A message that never became a turn has no
number to keep — the room line still names it.

No database: the reads are doubled by statement, so the assertions are about
what `close_overdue` writes, not about how the query is spelled.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.notice_meta import EVENT_TURN_TIMEOUT, SEVERITY_WARN, WHO_HUMAN
from app.domain.room_task import closing
from app.domain.room_task.closing import STOP_WORK_AFTER
from app.domain.room_task.models import Task, TaskStatus, TaskTitleSource


class _Rows(list):
    """What `execute` answers: a result with `.all()` over these rows."""

    def all(self):
        return list(self)


class _Savepoint:
    """`session.begin_nested()` — nothing here can fail, so no undo is owed."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Session:
    """The reads `close_overdue` and `unfinished_turns` make, by what they read.

    Doubling by statement rather than by call order keeps the test from
    breaking when an unrelated read is added ahead of these: each query is
    answered by the table it names, and a query this file does not expect
    would read as empty rather than as another query's rows.
    """

    def __init__(self, *, task, turns, asked=(), waiting=()):
        self._task = task
        self._turns = turns
        self._asked = asked
        self._waiting = waiting

    async def scalars(self, statement):
        sql = str(statement)
        if "deliveries" in sql or "agent_turns" in sql:
            # No write-up in progress, and no waiting message has been taken.
            return []
        if "blocks" in sql and "consumed_turn" in sql:
            return list(self._waiting)
        if "blocks" in sql:
            # Nothing was read and then failed.
            return []
        # The overdue scan: the one task this test closes.
        return [self._task.id]

    async def execute(self, statement):
        sql = str(statement)
        if "continuation_id" in sql:
            return _Rows(self._turns)
        if "max(" in sql:
            # Nothing to read about when each turn last spoke: both started
            # inside the silence limit, which is all `unfinished_turns` needs.
            return _Rows()
        return _Rows(self._asked)

    async def get(self, _model, _ident, **_kwargs):
        return self._task

    def begin_nested(self):
        return _Savepoint()


def _overdue_task() -> Task:
    """A task past `STOP_WORK_AFTER`: past this point the close stops it all."""
    task = Task()
    task.id = uuid.uuid4()
    task.title = "被强关的活"
    task.title_source = TaskTitleSource.human
    task.room_id = uuid.uuid4()
    task.status = TaskStatus.open
    task.closing_since = datetime.now(UTC) - STOP_WORK_AFTER - timedelta(minutes=1)
    return task


def _the_room() -> tuple[_Session, Task, uuid.UUID, uuid.UUID]:
    """A task closing on two running turns and one message no turn took.

    The first turn said what it was doing in its own prompt; the second one
    was begun by a message and has said nothing yet, so its prompt is the
    message it took.
    """
    task = _overdue_task()
    speaking, started_by_message = uuid.uuid4(), uuid.uuid4()
    started_at = datetime.now(UTC) - timedelta(seconds=30)
    turns = [
        SimpleNamespace(
            id=speaking,
            continuation_id=None,
            started_at=started_at,
            content="把接口改完",
        ),
        SimpleNamespace(
            id=started_by_message,
            continuation_id=None,
            started_at=started_at + timedelta(seconds=1),
            content=None,
        ),
    ]
    asked = [(started_by_message, "跑一遍测试")]
    waiting = SimpleNamespace(id=uuid.uuid4(), content="顺带把文档补一下", meta={})
    return (
        _Session(task=task, turns=turns, asked=asked, waiting=[waiting]),
        task,
        speaking,
        started_by_message,
    )


async def test_a_forced_close_keeps_every_stopped_turn_and_records_it(monkeypatch):
    session, task, speaking, started_by_message = _the_room()

    async def closed_now(_session, task_, _work, **_kwargs):
        task_.status = TaskStatus.closed
        return task_

    kept = []

    async def keep(_session, **kwargs):
        kept.append(kwargs)

    monkeypatch.setattr(closing, "close_task", closed_now)
    # `raising=False`: on the tree without the fix this name does not exist,
    # and the test should fail on what was not written, not on the setup.
    monkeypatch.setattr(closing, "keep_record", keep, raising=False)

    overdue = await closing.close_overdue(session, work=object())

    assert len(overdue) == 1, "the overdue task was not closed"
    undelivered = overdue[0].undelivered
    assert [work.prompt for work in undelivered] == [
        "把接口改完",
        "跑一遍测试",
        "顺带把文档补一下",
    ], "the prompts stopped by the close were not all kept"
    assert [work.turn_id for work in undelivered] == [
        speaking,
        started_by_message,
        None,
    ], "a stopped turn lost its number, or a waiting message grew one"

    assert [record["turn_id"] for record in kept] == [
        speaking,
        started_by_message,
    ], "the run records do not carry the stopped turns' own numbers"
    assert {record["conversation_id"] for record in kept} == {task.id}
    assert [record["meta"]["event_type"] for record in kept] == [
        EVENT_TURN_TIMEOUT,
        EVENT_TURN_TIMEOUT,
    ]
    assert [record["meta"]["severity"] for record in kept] == [
        SEVERITY_WARN,
        SEVERITY_WARN,
    ]
    assert [record["meta"]["who"] for record in kept] == [WHO_HUMAN, WHO_HUMAN]
    assert "把接口改完" in kept[0]["meta"]["detail"]
    assert "跑一遍测试" in kept[1]["meta"]["detail"]


async def test_unfinished_still_answers_with_the_prompts_alone():
    """`review.task_landing` reads this signature: prompts, in order."""
    session, task, _speaking, _started_by_message = _the_room()

    assert await closing.unfinished(session, task.id) == [
        "把接口改完",
        "跑一遍测试",
        "顺带把文档补一下",
    ]
