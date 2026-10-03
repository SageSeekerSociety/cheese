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
- ``self._hook_work`` / ``self._active_turn_ids`` → ``hook_work`` /
  ``active_turn_ids``：这一轮的现场状态（```chat._HookWorkState````）。本模块只读它
  两个字段里的一处 —— 这一轮是谁在做，用来给事件署名。

``ChatService`` 上留一行同名委托，``chat.py`` 重新导出这里的每个名字，所以调用点与
测试都不用改（``runtime.py`` 经 ``chat_service.post_system_event`` 说的那些话，走的
还是这条线）。
"""

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.sentences import say
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
from app.domain.agent.queries import _agent_handle, _block_payload
from app.domain.agent.service import (
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
)
from app.domain.agent.step_output import output_tail
from app.domain.agent.tool_preview import tool_detail, tool_preview, work_subpath
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.room_task.models import Task

logger = logging.getLogger(__name__)

#: 现场状态查表的键：一间房 + 这一轮。
TurnKey = tuple[uuid.UUID, uuid.UUID]

#: How far back a turn's change summary walks the topic branch's history.
#: Rendering the diff is `event_lines.py`'s job; how far back to look is this
#: side's (the git half is environment, that half is the contract).
_CHANGE_COMMIT_WALK = 30


class _TurnActor(Protocol):
    """``chat._HookWorkState`` 里本模块用到的唯一一个字段。

    「这一轮是谁在做」是署名的依据：几位队友同坐一间房时，拿房间的默认队友署名会
    把现场整轮记到别人头上。类型写在这里而不是 import ``chat``：那个方向是环。
    """

    acting_agent: str


async def post_system_event(
    sessions: async_sessionmaker,
    topic_id: uuid.UUID,
    content: str,
    turn_id: uuid.UUID | None = None,
    *,
    meta: dict | None = None,
) -> dict | None:
    """Persist a system event into the room (e.g. a turn failure): visible in
    the conversation, scrolls with it, and survives a reload — unlike a
    transient banner. It carries no ``meta.in_room``, and absent means shown,
    which is the whole point of this call: the platform says it out loud.
    Returns the block payload, or None if the topic died.

    Room-only: every caller here reports something about the room itself
    (a turn that failed, an environment that was rebuilt), which nobody was
    named for. A notice that knows whom it points at passes `points_at`
    to `announce` directly (`points_at=Event(...)`)."""
    async with sessions() as session:
        block = await announce(
            session,
            place_id=topic_id,
            content=content,
            meta=meta,
            turn_id=turn_id,
        )
        if block is None:
            return None
        payload = _block_payload(BlockOut.model_validate(block))
        await session.commit()
    return payload


async def _persist_room_event(
    sessions: async_sessionmaker,
    hook_work: Mapping[TurnKey, _TurnActor],
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
    task_id: uuid.UUID | None = None,
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
    state = hook_work.get((topic_id, turn_id)) if turn_id is not None else None
    async with sessions() as session:
        blocks = BlockRepository(session)
        if eid and await blocks.has_eid(topic_id, eid):
            return None
        # 「关于什么」由 `task_id` 推出，调用方不另声明：调用方说出这条事件
        # 关于什么的方式**就是**递不递一张卡下来（变更提醒从不递）。再收一个
        # about 形参，是同一个事实在一处声明两遍——不加 `about_kind` 列的同一条理由。
        landed = landing(
            EventAbout.task if task_id is not None else EventAbout.room,
            project_id=project_id,
            room_id=topic_id,
            task_id=task_id,
        )
        block = await blocks.add(
            project_id=landed.project_id,
            topic_id=landed.topic_id,
            task_id=landed.task_id,
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
    hook_work: Mapping[TurnKey, _TurnActor],
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    name: str,
    tool_input: dict,
    platform: bool,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    task_id: uuid.UUID | None = None,
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
        hook_work,
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
        task_id=task_id,
        author=author,
        at=at,
    )


async def _persist_subagent_result(
    sessions: async_sessionmaker,
    hook_work: Mapping[TurnKey, _TurnActor],
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    event: AgentToolResult,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    task_id: uuid.UUID | None = None,
) -> dict | None:
    """Land a returning subagent's conclusion in the room timeline."""
    return await _persist_room_event(
        sessions,
        hook_work,
        project_id=project_id,
        topic_id=topic_id,
        content=_subagent_event_text(event.description, event.text),
        meta=_subagent_result_meta(event.name, event.description, event.text),
        turn_id=turn_id,
        eid=eid or event.eid,
        platform_unsolicited=platform_unsolicited,
        task_id=task_id,
        author=event.agent_handle,
    )


async def _persist_worker_event(
    sessions: async_sessionmaker,
    hook_work: Mapping[TurnKey, _TurnActor],
    active_turn_ids: Mapping[uuid.UUID, set[uuid.UUID]],
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    event: AgentSubagentStart | AgentSubagentStop,
    task_id: uuid.UUID | None,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
) -> dict | None:
    """A worker started, or handed something back — on ITS thread's line.

    Nothing is written for a sub-thread whose label names no card here, and
    that is not tidiness. Measured twice on 2.1.224: after the session's own
    Stop, a SubagentStop arrives with an id matching no worker we saw, an
    empty label, and a fragment of a prompt where the closing message is —
    something inside Claude Code, not work anybody dispatched. Writing those
    would put a stranger's half-sentence in a room as if 芝士 had said it.

    A Stop is "handed something back", never "done": the same worker reports
    finished again after it resumes. So this is an event on the timeline and
    nothing more: acceptance closes delivered work, while an explicit close
    abandons a task. This event does neither.
    """
    if task_id is None:
        return None
    if isinstance(event, AgentSubagentStart):
        # 谁在做这张卡，是平台看见它开工的时候记下来的 —— 这条事件是第一个说
        # 出这个分身 id 的东西（id 在容器里才诞生，派活的时候没有任何东西能提
        # 前说出它）。卡上从此有一个分身在做，看板也就能问它还活着没有。
        await _note_worker(
            sessions,
            hook_work,
            active_turn_ids,
            task_id,
            event.agent_id,
            topic_id=topic_id,
            turn_id=turn_id,
            parent_session_id=event.session_id,
        )
        # The platform's own sentence about a worker, not anybody's words —
        # so `platform`, the same as every other line the platform says out
        # loud. Attributing it to 芝士 would make the room's history contain
        # a remark 芝士 never made.
        content, author_type = say("subagentStart"), AuthorType.platform
        meta: dict = {"event_type": "subagent_start"}
    else:
        # The closing message in full, and it IS the worker's own words. It
        # reaches the platform exactly once, here — the room's transcript
        # does not contain it and the worker's dies with its container.
        content = event.text.strip() or say("subagentStopEmpty")
        author_type = AuthorType.participant
        meta = {"event_type": "subagent_stop"}
        if event.transcript_path:
            meta["transcript_path"] = event.transcript_path
        # 结论落在卡上, overwriting the previous stop's — the newest is what
        # the room reads when it decides whether the work is done. Only for
        # a sub-thread whose label names this card (`task_id` is that check,
        # above), so the fragments Claude Code's own internal agents stop
        # with never become anybody's conclusion.
        await _record_conclusion(sessions, task_id, event.text.strip())
    meta["agent_id"] = event.agent_id
    if event.thread_label:
        meta["thread_label"] = event.thread_label
    return await _persist_room_event(
        sessions,
        hook_work,
        project_id=project_id,
        topic_id=topic_id,
        content=content,
        meta=meta,
        turn_id=turn_id,
        eid=eid,
        platform_unsolicited=platform_unsolicited,
        task_id=task_id,
        author_type=author_type,
        # Shown in the thread rather than kept to 现场: what a worker handed
        # back is the whole reason anybody opens the thread.
        in_room=True,
    )


async def _persist_change_summary(
    sessions: async_sessionmaker,
    hook_work: Mapping[TurnKey, _TurnActor],
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
        hook_work,
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


async def _note_worker(
    sessions: async_sessionmaker,
    hook_work: Mapping[TurnKey, _TurnActor],
    active_turn_ids: Mapping[uuid.UUID, set[uuid.UUID]],
    task_id: uuid.UUID,
    subagent_id: str,
    *,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID | None,
    parent_session_id: str | None,
) -> None:
    """把做这条活的分身记在卡上。"""
    from app.domain.delivery.agent import instance_for_seat
    from app.domain.room_task.services import TaskService

    if turn_id is None or not parent_session_id:
        return
    async with sessions() as session:
        tasks = TaskService(session)
        task = await session.get(Task, task_id, with_for_update=True)
        state = hook_work.get((topic_id, turn_id))
        if (
            task is None
            or task.room_id != topic_id
            or state is None
            or not parent_session_id
        ):
            return
        instance = await instance_for_seat(session, task.project_id, state.acting_agent)
        # A delayed start from a replaced parent may remain historical
        # evidence, but cannot acquire control of the task's current worker.
        if turn_id not in active_turn_ids.get(topic_id, ()) or instance is None:
            return
        task.execution_agent_instance_id = instance.id
        task.execution_parent_session_id = parent_session_id
        task.execution_turn_id = turn_id
        await tasks.note_worker(task, subagent_id)
        await session.commit()


async def _record_conclusion(
    sessions: async_sessionmaker, task_id: uuid.UUID, text: str
) -> None:
    from app.domain.room_task.services import TaskService

    if not text:
        return
    async with sessions() as session:
        tasks = TaskService(session)
        task = await tasks.get(task_id)
        if task is None:
            return
        await tasks.record_conclusion(task, text)
        await session.commit()


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
    _HookWorkState.known_commits)."""
    try:
        from app.domain.repository.forge_files import ProjectFiles
        from app.domain.room_task.services import TaskService

        async with sessions() as session:
            tasks = await TaskService(session).list_in_room(topic_id)
            commits = set()
            for task in tasks:
                if task.branch_name:
                    history = await ProjectFiles(session, project_id, task.id).history()
                    commits.update(row["sha"] for row in history[-_CHANGE_COMMIT_WALK:])
            return commits
    except Exception:  # noqa: BLE001 — no baseline just means no summary
        logger.warning("commit baseline unreadable for topic %s", topic_id)
        return None
