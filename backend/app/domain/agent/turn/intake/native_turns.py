"""Prepare a recovered or self-started native turn's actual completion context.

Scheduling stays with the runner via one explicit opening capability. The
read/own interval and context creation preserve their original exception scope.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.agent.queries import _acting_handle, _agent_at, _session_agent
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.agent_instance.services import AgentInstanceService, memory_pool
from app.domain.project.reads import load_project
from app.domain.room_task.place import PlaceResolver

logger = logging.getLogger(__name__)


class OpenSessionTurn(Protocol):
    async def __call__(
        self,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        agent_handle: str,
        session_id: str | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class NativeTurns:
    sessions: async_sessionmaker
    live: LiveWork
    open_turn: OpenSessionTurn
    known_commits: Callable[[uuid.UUID, uuid.UUID], Awaitable[set[str] | None]]

    async def begin(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        opened: bool = False,
        agent_handle: str | None = None,
        session_id: str | None = None,
    ) -> "HookWorkState | None":
        """Give a turn the session started for itself the context to end like
        any other: an interval a sweep can find, and everything its Stop needs.

        Without this, a self-started turn's Stop landed the message and then did
        nothing at all — no usage row, no conclusion cards settled, no change
        summary, and no interval to close, because none was ever opened. The
        room could not even tell you the turn had happened.

        署名是这个房间的席位 handle。退场的是那个谁也没写过的作者值（字面量就是
        「会话」两个字，结论 13），不是这条记录 —— 一个 worker 做完唤醒主线程，
        那是同一个 handle 两条线程之间的一条便条，发件人就在这儿。

        Read rather than assembled: there is no prompt to build here, so this
        takes only what turn END needs, and takes it in one transaction. Two
        fields are deliberately not read — `roster` stays None so the message
        path loads it (passing [] would mean 私聊 and flag every @ as a
        non-member), and `pending_ids` stays empty because nothing was fed to
        this turn. A message that merges into it mid-flight is stamped consumed
        by its own receipt (`confirm_prompt_receipt`), not from here.

        ``opened`` is a turn whose interval already exists: one a previous
        process of ours fed and delivered, which outlived it and is found again
        by recovery. It gets the same context and no second row, and what the
        process that assembled it wrote on that row: where its model traffic
        went, the message it answers, and when it really started.

        ``agent_handle`` is the agent whose session this is, when the caller
        knows it. Recovery does — it is on the session's own key — and must pass
        it: the room's fallback is the project default, and a teammate's turn
        recorded under the default authors every block of that turn under the
        wrong seat.

        Returns None if the place is gone or the bookkeeping write fails; the
        event that triggered this still lands, exactly as it did before.
        """
        try:
            async with self.sessions() as session:
                place = await PlaceResolver(session).conversation(topic_id)
                if place is None:
                    return None
                topic = place.room
                project = await load_project(session, project_id)
                if project is None:
                    return None
                agents = AgentInstanceService(session)
                agent = (
                    await _agent_at(session, place)
                    if place.task is not None and agent_handle is None
                    else await _session_agent(agents, topic, project, agent_handle)
                )
                agent_pool = memory_pool(topic.project_id, agent)
                # 署这个会话所属队友的名，不是房间的默认队友：一间坐着几位队友
                # 的房间里，别人的会话自己开的一轮署成默认那位，现场和「正在处
                # 理」就会把干活的人认错。
                acting_agent = await _acting_handle(session, place.room_id, agent)
                # Read whether or not this process opened it: a session keeps
                # working across a backend restart, and the process that fed or
                # first saw the turn wrote what it knew on the row.
                row = await AgentTurnRepository(session).get(turn_id)
            if not opened:
                await self.open_turn(
                    topic_id,
                    turn_id,
                    author=acting_agent,
                    agent_handle=agent.handle,
                    session_id=session_id,
                )
        except Exception:  # noqa: BLE001 — the event matters more than the row
            logger.exception(
                "could not open the turn a session started for itself "
                "(topic=%s, work=%s)",
                topic_id,
                turn_id,
            )
            return None
        state = HookWorkState(
            project_id=project_id,
            topic_id=topic_id,
            work_id=turn_id,
            pending_ids=set(),
            reply_to=row.reply_to if row is not None else None,
            roster=None,
            topic_refs=[],
            continuation_id=turn_id,
            # "native" when this process has never assembled a turn for this
            # session (a screen recovered on the way up, say). It is the answer
            # that cannot invent spend: the gateway's log is drained by whatever
            # turn closes next, which is exactly what happened before any of
            # this existed.
            route=(row.route if row is not None else None)
            or self.live.session_route.get(topic_id, "native"),
            model=self.live.session_model.get(topic_id, ""),
            acting_agent=acting_agent,
            agent_pool=agent_pool,
            user_text="",
            started_at=row.started_at if row is not None else datetime.now(UTC),
            agent_instance_handle=agent.handle,
            known_commits=asyncio.ensure_future(
                self.known_commits(project_id, place.room_id)
            ),
            self_started=not opened,
        )
        self.live.hook_work[(topic_id, turn_id)] = state
        if opened:
            # The session announced this turn's start to the process that fed
            # it, so this one never heard it: without this a message sent now
            # would start a turn beside it instead of joining it.
            # The single-slot world OVERWROTE the slot here, silently dropping
            # a stale leftover; keep that hygiene by pruning ids whose hook
            # state is gone — a genuinely live turn always has its state and
            # survives, which is what several agents in one room will need.
            self.live.active_turn_ids[topic_id] = {
                work_id
                for work_id in self.live.active_turn_ids.get(topic_id, ())
                if (topic_id, work_id) in self.live.hook_work
            } | {turn_id}
        return state
