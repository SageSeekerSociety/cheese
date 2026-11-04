"""这一轮在房间里落下的那些行：系统事件块、变更汇总 —— 落一块提交一次。

``ChatService`` 上这一族方法回答的是同一个问题：**一轮跑到一半，房间该知道什么，
现在就要知道**。这一轮改了哪些文件、平台自己要说的那句系统事件 —— 它们都不等整轮
结束才攒成一条，而是一个一个地落成块、当场提交（中途崩溃或重启时，时间线上已经
产生的那一段不会丢），再广播出去。

搬出来时按原样搬 —— 入参出参就是它们与调用方之间全部的约定，行为一格没动。少数
形状变化只有两处，都是「没有 `self` 可用了」：

- ``self._sessions`` → ``sessions``：一个 sessionmaker，事务边界逐字不变 —— 原来在
  哪里 ``async with self._sessions()``，现在还在哪里；
- ``self._hook_work`` → ``live``：这一轮的现场状态（``live_work.LiveWork``）。本模块
  只读它一处 —— 这一轮是谁在做，用来给事件署名。

``ChatService`` 上留一行同名委托，``chat.py`` 重新导出这里的每个名字，所以调用点与
测试都不用改（``runtime.py`` 经 ``chat_service.post_system_event`` 说的那些话，走的
还是这条线）。
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.announce import announce
from app.domain.agent.event_lines import (
    _Changeset,
    _diff_file_stats,
)
from app.domain.agent.models import AgentTurn
from app.domain.agent.queries import _block_payload
from app.domain.agent.run_records import record_now
from app.domain.block.schemas import BlockOut
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import recipient_seat
from app.domain.notice_meta import RUN_RECORD_EVENTS
from app.domain.room_task.place import PlaceResolver

logger = logging.getLogger(__name__)

#: How far back a turn's change summary walks the topic branch's history.
#: Rendering the diff is `event_lines.py`'s job; how far back to look is this
#: side's (the git half is environment, that half is the contract).
_CHANGE_COMMIT_WALK = 30

#: How many of a channel's open tasks have their history read at once. Each one
#: is a forge round trip, and the baseline is read in the turn's tail; a channel
#: a dozen tasks deep used to spend that tail on them one after another.
_BASELINE_CONCURRENCY = 6


async def _turn_seat(session: AsyncSession, turn_id: uuid.UUID) -> str | None:
    """Whose turn ``turn_id`` is: the seat the turn recorded, or, for a turn
    that has no row yet (still queued), the seat its delivery addresses — the
    delivery's attempt is that turn."""
    turn = await session.get(AgentTurn, turn_id)
    if turn is not None and turn.agent_handle:
        return turn.agent_handle
    delivery = await session.scalar(
        select(Delivery).where(Delivery.attempt_id == turn_id)
    )
    if delivery is None:
        return None
    return recipient_seat(
        {"instance_id": delivery.agent_instance_id, "handle": delivery.recipient_handle}
    )


async def post_system_event(
    sessions: async_sessionmaker,
    topic_id: uuid.UUID,
    content: str,
    turn_id: uuid.UUID | None = None,
    *,
    meta: dict | None = None,
    inner_id: uuid.UUID | None = None,
) -> dict | None:
    """Persist a system event into the room (e.g. a turn failure): visible in
    the conversation, scrolls with it, and survives a reload — unlike a
    transient banner. It carries no ``meta.in_room``, and absent means shown,
    which is the whole point of this call: the platform says it out loud.
    Returns the block payload, or None if the topic died.

    The platform's own running (`RUN_RECORD_EVENTS`: a turn queued, a message
    sent back to the queue) is not said: it is kept as a run record for the
    现场, published there, and nothing is returned.

    Room-only: every caller here reports something about the room itself
    (a turn that failed, an environment that was rebuilt), which nobody was
    named for. A notice that knows whom it points at passes `points_at`
    to `announce` directly (`points_at=Event(...)`).

    The line stays the platform's: a machine's error under a teammate's name
    reads as that teammate's judgement. It records in ``meta.seat`` whose turn
    it is about (`_turn_seat`), and the room shows it beside that teammate. A
    room may seat several, so nothing else can say which one it was."""
    if (meta or {}).get("event_type") in RUN_RECORD_EVENTS:
        await _keep_run_record(
            sessions, inner_id or topic_id, content, turn_id, meta=meta or {}
        )
        return None
    async with sessions() as session:
        seat = await _turn_seat(session, turn_id) if turn_id is not None else None
        block = await announce(
            session,
            place_id=topic_id,
            content=content,
            meta={**(meta or {}), "seat": seat} if seat else meta,
            turn_id=turn_id,
            task_id=inner_id,
            published_by_caller=True,
        )
        if block is None:
            return None
        payload = _block_payload(BlockOut.model_validate(block))
        await session.commit()
    return payload


async def say_in(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
    turn_id: uuid.UUID,
    *,
    meta: dict,
) -> dict | None:
    """`post_system_event` for one turn, written in the caller's transaction,
    so the line lands with whatever the caller decides with it or not at all.
    The caller commits and publishes the returned payload; None when the
    conversation is gone."""
    place = await PlaceResolver(session).conversation(conversation_id)
    if place is None:
        return None
    seat = await _turn_seat(session, turn_id)
    block = await announce(
        session,
        place_id=place.room_id,
        content=content,
        meta={**meta, "seat": seat} if seat else meta,
        turn_id=turn_id,
        task_id=place.inner_id,
        published_by_caller=True,
    )
    return None if block is None else _block_payload(BlockOut.model_validate(block))


async def _keep_run_record(
    sessions: async_sessionmaker,
    conversation_id: uuid.UUID,
    content: str,
    turn_id: uuid.UUID | None,
    *,
    meta: dict,
) -> None:

    seat = None
    if turn_id is not None:
        try:
            async with sessions() as session:
                seat = await _turn_seat(session, turn_id)
        except Exception:  # the record matters more than its seat
            logger.exception("could not read whose turn %s is", turn_id)
    await record_now(
        sessions,
        conversation_id=conversation_id,
        content=content,
        meta=meta,
        turn_id=turn_id,
        seat=seat,
    )


async def _turn_changeset(
    sessions: async_sessionmaker,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    known_commits: set[str] | None,
) -> _Changeset | None:
    """This turn's net effect on the topic branch, or None when there is none.

    The agent commits and pushes its own work, so "the commits that were not
    there at turn start" is exactly what this turn delivered. Best-effort and
    off the event loop: the numbers are a courtesy, and no turn should die
    (or stall) over them.
    """
    if known_commits is None:
        return None

    commits = await _known_commits(sessions, project_id, topic_id)
    if commits is None:
        return None

    async def _collect() -> _Changeset | None:
        # deferred-import: tests replace this name on app.domain.repository.forge_files
        from app.domain.repository.forge_files import ProjectFiles

        fresh = [h for h in commits if h not in known_commits]
        if not fresh:
            return None
        totals: dict[str, dict] = {}
        async with sessions() as session:
            files = ProjectFiles(session, project_id, None)
            for sha in fresh:
                for entry in _diff_file_stats(await files.commit_diff(sha)):
                    acc = totals.setdefault(
                        entry["path"],
                        {"path": entry["path"], "added": 0, "removed": 0},
                    )
                    acc["added"] += entry["added"]
                    acc["removed"] += entry["removed"]
        files = sorted(
            totals.values(), key=lambda f: (-(f["added"] + f["removed"]), f["path"])
        )
        if not files:
            return None  # a commit that changed nothing (empty snapshot)
        return _Changeset(commits=fresh, files=files)

    try:
        return await _collect()
    except Exception:  # never fail a turn over its summary
        logger.warning("change summary failed for topic %s", topic_id)
        return None


async def _known_commits(
    sessions: async_sessionmaker, project_id: uuid.UUID, topic_id: uuid.UUID
) -> set[str] | None:
    """The topic branch's commits right now — the baseline the turn's change
    summary is measured against. None when it cannot be read (see
    HookWorkState.known_commits)."""
    try:
        # deferred-import: tests replace this name on app.domain.repository.forge_files
        from app.domain.repository.forge_files import ProjectFiles

        # deferred-import: tests replace this name on app.domain.room_task.services
        from app.domain.room_task.services import TaskService

        async with sessions() as session:
            # A closed task takes no more commits, and its branch is often
            # deleted; a channel holds every task its project ever ran.
            tasks = await TaskService(session).open_branches_in_room(topic_id)
            task_ids = [task.id for task in tasks]

        # The ids, not the rows: that session is closed before the walks start,
        # and one AsyncSession is not driven from two coroutines at once, so
        # each walk reads its branch in a session of its own.
        limit = asyncio.Semaphore(_BASELINE_CONCURRENCY)

        async def _walk(task_id: uuid.UUID) -> list[str]:
            async with limit, sessions() as session:
                # Each history is a forge request: the connection goes back
                # to the pool while it is in flight.
                history = await ProjectFiles(
                    session, project_id, task_id, release_session=True
                ).history()
            return [row["sha"] for row in history[-_CHANGE_COMMIT_WALK:]]

        walking = [asyncio.create_task(_walk(task_id)) for task_id in task_ids]
        try:
            walked = await asyncio.gather(*walking)
        except BaseException:
            # One unreadable branch costs the whole baseline, as it did when the
            # walks were one after another; stop the rest so nothing outlives
            # the turn's tail.
            for task in walking:
                task.cancel()
            raise
        commits = set()
        for shas in walked:
            commits.update(shas)
        return commits
    except Exception:  # no baseline just means no summary
        logger.warning(
            "commit baseline unreadable for topic %s", topic_id, exc_info=True
        )
        return None
