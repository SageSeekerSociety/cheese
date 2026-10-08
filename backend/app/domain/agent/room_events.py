"""这一轮在房间里落下的那些行：事件块、步骤的判决、变更汇总 —— 落一块提交一次。

``ChatService`` 上这一族方法回答的是同一个问题：**一轮跑到一半，房间该知道什么，
现在就要知道**。工具的调用、分身交回的结论、哪位队友开工了、一个步骤红了、这一轮
改了哪些文件、平台自己要说的那句系统事件 —— 它们都不等整轮结束才攒成一条，而是一
个一个地落成块、当场提交（中途崩溃或重启时，时间线上已经产生的那一段不会丢），再
广播出去。

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

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.announce import announce
from app.domain.agent.event_lines import (
    _change_summary_meta,
    _Changeset,
    _diff_file_stats,
    _format_change_summary,
    _format_tool_event,
    _subagent_event_text,
    _subagent_result_meta,
    _tool_event_meta,
)
from app.domain.agent.models import AgentTurn
from app.domain.agent.platform_notices import RUN_RECORD_EVENTS
from app.domain.agent.queries import _agent_handle, _block_payload
from app.domain.agent.service import (
    AgentToolResult,
)
from app.domain.agent.step_output import output_tail
from app.domain.agent.tool_preview import tool_detail, tool_preview, work_subpath
from app.domain.agent.turn.state.live import LiveWork
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import recipient_seat

logger = logging.getLogger(__name__)

#: How far back a turn's change summary walks the topic branch's history.
#: Rendering the diff is `event_lines.py`'s job; how far back to look is this
#: side's (the git half is environment, that half is the contract).
_CHANGE_COMMIT_WALK = 30


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


async def _keep_run_record(
    sessions: async_sessionmaker,
    conversation_id: uuid.UUID,
    content: str,
    turn_id: uuid.UUID | None,
    *,
    meta: dict,
) -> None:
    from app.domain.agent.run_records import record_now

    seat = None
    if turn_id is not None:
        try:
            async with sessions() as session:
                seat = await _turn_seat(session, turn_id)
        except Exception:  # noqa: BLE001 — the record matters more than its seat
            logger.exception("could not read whose turn %s is", turn_id)
    await record_now(
        sessions,
        conversation_id=conversation_id,
        content=content,
        meta=meta,
        turn_id=turn_id,
        seat=seat,
    )


async def _persist_room_event(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    content: str,
    meta: dict,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    in_room: bool = False,
    author_type: AuthorType = AuthorType.participant,
    inner_id: uuid.UUID | None = None,
    author: str | None = None,
    at: datetime | None = None,
) -> dict | None:
    """One event block, committed NOW and deduped by event-id.

    Shared by everything the room learns mid-turn — a tool call, a subagent's
    conclusion, the turn's change summary — so all three get the same
    durability and idempotency contract instead of three copies of it that
    drift. Returns None when this event-id already landed.

    ``in_room`` decides whether the conversation shows it at all, and it
    travels as ``meta.in_room`` — its own field, because visibility is not
    authorship. It used to ride on ``author_type``, which meant an event
    genuinely written by 芝士 could not be shown in the room without lying
    about who wrote it, and anything that later wanted to know the author
    was reading a field answering a different question. Absent means shown:
    every other writer in the codebase posts to the room.

    ``author_type`` is then free to answer its own question, and does: 芝士
    is a participant and wrote the tool calls and the subagent conclusions,
    while the change summary is the platform's own line."""
    meta = {**meta, "in_room": in_room}
    if eid:
        meta = {**meta, "eid": eid}
    if platform_unsolicited:
        meta = {**meta, "platform_unsolicited": True}
    # 这一步是这一轮的执行者做的，署它的名。房间的默认队友只是没有这一轮账目
    # 时的回落：几位队友同坐一间房时，拿默认那位署名会把现场整轮记到别人头上。
    # A turn is kept under its conversation: the task's when there is one.
    state = (
        live.hook_work.get((inner_id or topic_id, turn_id))
        if turn_id is not None
        else None
    )
    async with sessions() as session:
        blocks = BlockRepository(session)
        if eid and await blocks.has_eid(inner_id or topic_id, eid):
            return None
        # 「关于什么」由 `inner_id` 推出，调用方不另声明：调用方说出这条事件
        # 关于什么的方式**就是**递不递一张卡下来（变更提醒从不递）。再收一个
        # about 形参，是同一个事实在一处声明两遍——不加 `about_kind` 列的同一条理由。
        landed = landing(
            EventAbout.task if inner_id is not None else EventAbout.room,
            project_id=project_id,
            room_id=topic_id,
            task_id=inner_id,
        )
        block = await blocks.add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=(
                author
                or (state.acting_agent if state is not None else None)
                or await _agent_handle(session, topic_id)
            ),
            author_type=author_type,
            content=content,
            kind=BlockKind.event,
            turn_id=turn_id,
            meta=meta,
            created_at=at,
        )
        payload = _block_payload(BlockOut.model_validate(block))
        await session.commit()
    return payload


async def _persist_tool_event(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    name: str,
    tool_input: dict,
    platform: bool,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    inner_id: uuid.UUID | None = None,
    author: str | None = None,
    at: datetime | None = None,
) -> dict | None:
    """Persist ONE 施工现场 event the moment it streams in, not batched to the
    turn-end tx2. Mirrors _persist_assistant_message's commit-now contract so
    a mid-turn restart/crash never loses the 现场 timeline already produced.
    ``eid`` (the harness's own id for the event) is stamped into meta so a
    record read twice lands once. Returns the persisted block payload so the
    caller can broadcast it as a WS frame."""
    preview = tool_preview(
        name, tool_input, work_dir=work_subpath(project_id, topic_id)
    )
    return await _persist_room_event(
        sessions,
        live,
        project_id=project_id,
        topic_id=topic_id,
        content=_format_tool_event(name, preview),
        meta=_tool_event_meta(
            name,
            preview,
            platform=platform,
            detail=tool_detail(name, tool_input, preview),
        ),
        turn_id=turn_id,
        eid=eid,
        platform_unsolicited=platform_unsolicited,
        inner_id=inner_id,
        author=author,
        at=at,
    )


async def _persist_subagent_result(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    event: AgentToolResult,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    inner_id: uuid.UUID | None = None,
) -> dict | None:
    """Land a returning subagent's conclusion in the room timeline."""
    return await _persist_room_event(
        sessions,
        live,
        project_id=project_id,
        topic_id=topic_id,
        content=_subagent_event_text(event.description, event.text),
        meta=_subagent_result_meta(event.name, event.description, event.text),
        turn_id=turn_id,
        eid=eid or event.eid,
        platform_unsolicited=platform_unsolicited,
        inner_id=inner_id,
        author=event.agent_handle,
    )


async def _persist_change_summary(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID | None,
    changeset: _Changeset,
) -> dict | None:
    """Land 「这一轮改了 N 个文件」 in the room timeline.

    This one goes in the ROOM, not just 现场 (spec §8.5 变更提醒). What 芝士
    changed is the one thing about a turn that is nowhere else in the
    conversation: the doc panel lights up on its own and the accept card
    speaks for itself, but "this turn touched these files" was only ever a
    grey line in a drawer nobody has open."""
    return await _persist_room_event(
        sessions,
        live,
        project_id=project_id,
        topic_id=topic_id,
        content=_format_change_summary(changeset.files),
        meta=_change_summary_meta(changeset),
        turn_id=turn_id,
        in_room=True,
        author_type=AuthorType.platform,  # 平台自己数出来的，不是芝士说的
    )


async def _mark_step_failed(
    sessions: async_sessionmaker, block_id: uuid.UUID, error: str
) -> dict | None:
    """Stamp a 现场 step as failed, and hand back the step as it now reads.
    Never fails a turn over a red dot."""
    try:
        async with sessions() as session:
            block = await BlockRepository(session).mark_step_failed(block_id, error)
            payload = (
                _block_payload(BlockOut.model_validate(block))
                if block is not None
                else None
            )
            await session.commit()
        return payload
    except Exception:  # noqa: BLE001 — a step's verdict is not worth a turn
        logger.warning("could not mark step %s failed", block_id)
        return None


async def _record_step_output(
    sessions: async_sessionmaker, block_id: uuid.UUID, text: str
) -> dict | None:
    """Keep the tail of what a step printed. Never fails a turn over it."""
    output, total = output_tail(text)
    try:
        async with sessions() as session:
            block = await BlockRepository(session).record_step_output(
                block_id, output, total
            )
            payload = (
                _block_payload(BlockOut.model_validate(block))
                if block is not None
                else None
            )
            await session.commit()
        return payload
    except Exception:  # noqa: BLE001 — a step's output is not worth a turn
        logger.warning("could not record the output of step %s", block_id)
        return None


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
    except Exception:  # noqa: BLE001 — never fail a turn over its summary
        logger.warning("change summary failed for topic %s", topic_id)
        return None


async def _known_commits(
    sessions: async_sessionmaker, project_id: uuid.UUID, topic_id: uuid.UUID
) -> set[str] | None:
    """The topic branch's commits right now — the baseline the turn's change
    summary is measured against. None when it cannot be read (see
    HookWorkState.known_commits)."""
    try:
        from app.domain.repository.forge_files import ProjectFiles
        from app.domain.room_task.services import TaskService

        async with sessions() as session:
            # A closed task takes no more commits, and its branch is often
            # deleted; a channel holds every task its project ever ran.
            tasks = await TaskService(session).open_branches_in_room(topic_id)
            commits = set()
            for task in tasks:
                # Each history is a forge request: the connection goes back
                # to the pool while it is in flight.
                history = await ProjectFiles(
                    session, project_id, task.id, release_session=True
                ).history()
                commits.update(row["sha"] for row in history[-_CHANGE_COMMIT_WALK:])
            return commits
    except Exception:  # noqa: BLE001 — no baseline just means no summary
        logger.warning(
            "commit baseline unreadable for topic %s", topic_id, exc_info=True
        )
        return None
