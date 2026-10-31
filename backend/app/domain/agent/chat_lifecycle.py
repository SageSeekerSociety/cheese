"""ChatService 里管「这一台进程手上的活」的那一半。

四组事：停（`dismiss`、`stop_work`、`stop_work_in_closed_tasks`、`stop_listening`、
`_close_open_turns`）；问它还在不在（`has_running_turn`、`has_live_screen`、
`row_is_dead`、`seat_state`，都读权威的那行 `AgentTurn` 与算力那侧，不读本进程
缓存）；把「在跑」投到实时协议上（`_set_hook_activity`）；以及一次准入前要问的事实
（`work_policy`）和「这台机器够不着」那句话（`_note_reachability`）。

`_confirm_turn_row_open` / `_confirm_turn_row_closed` 是 `LiveWork` 的写穿回调：
叶子不认识库，这一层认识，所以它们和 `_sessions` 待在一起。

Mixin：方法仍挂在 `ChatService` 上——`_close_turn_interval` 被 `hook_events` 收成
回调，`_set_hook_activity` 被 `room/reads.py` 从服务上取，实例的 monkeypatch
（`chat.work_policy`）也照旧。
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent import death_evidence, turn_inputs
from app.domain.agent.models import STOP_STOPPED
from app.domain.agent.pending_messages import nudge_messages
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.turn.intake.notes import _note_reachability
from app.domain.agent.turn.state import authority
from app.domain.agent.work_policy import work_policy
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.services import threads_of_rooms
from app.domain.project.reads import load_project
from app.domain.room_task import naming
from app.domain.topic.reads import load_topic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.domain.agent.compute import ComputePool
    from app.domain.agent.turn.state.live import LiveWork

logger = logging.getLogger(__name__)


class TurnLifecycle:
    """停、存活、把状态投出去——`ChatService` 里管这台进程手上那点活的一半。

    属性和方法是服务的，这里声明出来，让类型检查看得见本 mixin 自己的契约。
    """

    if TYPE_CHECKING:
        _sessions: async_sessionmaker
        _compute: ComputePool
        live: LiveWork

    async def dismiss(self, topic_id: uuid.UUID, seat: str) -> None:
        """Stop the work the teammate on rosters as ``seat`` still has running
        in this room: one taken off the room, whose every call there is now
        refused. What it wrote so far stays."""
        if not seat or not seat.startswith("cheese-"):
            return
        async with self._sessions() as session:
            topic = await load_topic(session, topic_id)
            project = topic and await load_project(session, topic.project_id)
            agent = project and await AgentInstanceService(session).for_seat_handle(
                project, seat
            )
            if agent is None:
                return
            # It sat in the room for the room's 支线 too: their work stops as well.
            threads = list(await threads_of_rooms(session, [topic_id]))
        for conversation in (topic_id, *threads):
            await self._compute.dismiss(conversation, agent.handle)

    async def stop_work(self, conversation_id: uuid.UUID) -> None:
        """Stop whatever a teammate is still doing in this conversation, a
        task that has just closed. What it wrote so far stays."""
        await self._compute.stop_work(conversation_id)

    async def stop_work_in_closed_tasks(self) -> int:
        """Stop the work this process's sessions still do in closed tasks
        (`turn_inputs.stop_work_in_closed_tasks`)."""
        return await turn_inputs.stop_work_in_closed_tasks(
            self._sessions, self._compute
        )

    async def _close_turn_interval(
        self, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> None:
        """A native Stop may clear the seat after earlier queue nudges failed.

        Interval ownership and scheduling stay with this composition root.
        """
        await self._close_open_turns(topic_id, turn_id)
        nudge_messages(self, topic_id)

    def session_controls(self, topic_id: uuid.UUID):
        """The runtime whose live session in this room takes controls, if any."""
        return self._compute.session_controls(topic_id)

    async def recover_native_tools(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Platform tools back for this room; True when they had been gone."""
        return await self._compute.recover_native_tools(topic_id, agent_handle)

    async def has_running_turn(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Whether a turn is running in the topic — durably, across processes.

        The open ``AgentTurn`` row answers (`turn/state/authority.py`), not this
        process's cache: a teammate's turn from another backend is seen here, a
        finished one is not kept alive by a stale entry.
        """
        return await authority.has_running_turn(self._sessions, topic_id, agent_handle)

    async def _confirm_turn_row_open(
        self, topic_id: uuid.UUID, work_id: uuid.UUID
    ) -> bool:
        """The `LiveWork` write-through: confirm the turn's row is open.

        Reads only, and invents nothing: the row is the turn lifecycle's to open
        (`open_turn`), so a mark that lands before the row exists is told no and
        leaves the cache clear until the lifecycle commits the real row. A row a
        sweep closed stays closed. No database (unit tests) means cache only.
        """
        if self._sessions is None:
            return True
        return await authority.confirm_open(self._sessions, work_id)

    async def _confirm_turn_row_closed(
        self, topic_id: uuid.UUID, work_id: uuid.UUID
    ) -> bool:
        """The `LiveWork` release check: did the lifecycle end this turn?

        Closing is the lifecycle's; a row still open when the cache lets go is
        the one divergence worth a warning (raised in `mark_turn_inactive`).
        """
        if self._sessions is None:
            return True
        return await authority.confirm_closed(self._sessions, work_id)

    @asynccontextmanager
    async def edit_environment(self, topic_id: uuid.UUID) -> AsyncIterator[None]:
        """Prevent a new prompt from racing an explicit environment change."""
        lock = self.live.lock_for(topic_id)
        if lock.locked() or await self.has_running_turn(topic_id):
            raise ValidationError(say("roomBusyFinishBeforeEnvironment"))
        async with lock:
            if await self.has_running_turn(topic_id):
                raise ValidationError(say("roomBusyRetryLater"))
            yield

    async def work_policy(
        self, topic_id: uuid.UUID, agent_instance_id: uuid.UUID | None = None
    ) -> dict | None:
        """Admission facts the AgentWorkRunner gates on BEFORE running a turn,
        for the agent it is addressed to when that is known."""
        return await work_policy(
            self._sessions, self._compute, topic_id, agent_instance_id
        )

    async def _close_open_turns(self, topic_id: uuid.UUID, turn_id: uuid.UUID) -> None:
        """End the one open interval the Stop names (FB-56). Never raises — a
        Stop that cannot update the bookkeeping must still land the message it
        carries. An id that names no live row closes nothing."""
        try:
            async with self._sessions() as session:
                closed = await AgentTurnRepository(session).close_one(
                    topic_id, turn_id, datetime.now(UTC), reason=STOP_STOPPED
                )
                if closed:
                    await session.commit()
        except Exception:  # the Stop matters more than the row
            logger.exception("could not close open turns for topic %s", topic_id)

    def row_is_dead(
        self, topic_id: uuid.UUID, agent_handle: str, session_id: str | None
    ) -> bool:
        """Is THIS row's conversation known dead (FB-56 legacy③)?"""
        return death_evidence.row_is_dead(
            self.live, self._compute, topic_id, agent_handle, session_id
        )

    def seat_state(self, topic_id: uuid.UUID, agent_handle: str) -> str:
        """One of "live" / "dead" / "unknown" for the seat (FB-56 legacy③)."""
        return death_evidence.seat_state(
            self.live, self._compute, topic_id, agent_handle
        )

    def draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool:
        """Is this seat's runner another release's, finishing its work? Its
        messages wait until it has been replaced (``ComputePool.draining``)."""
        return self._compute.draining(topic_id, agent_handle)

    def tell_draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool:
        """Whether the room is to be told now that a message waits for this
        seat's drain: once per drain."""
        return self._compute.tell_draining(topic_id, agent_handle)

    def has_live_screen(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Is a session for this topic still reachable? The orphan sweep's first
        question, and the one that used to be unanswerable.

        With ``agent_handle`` it is that agent's session: a room seats several
        teammates, and one of them still answering says nothing about another.
        """
        return self._compute.holds(topic_id, agent_handle)

    async def turns_that_produced_something(
        self, turn_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Which of these turns own at least one AI-authored block.

        Second-hand proof that the prompt arrived, and a narrow backstop rather
        than a source: the runtime records delivery first-hand the moment the
        transport accepts the write, so the only thing this covers is a process
        dying between that write and the record of it. Without it, that
        millisecond re-sends a task 芝士 is already working on.
        """
        if not turn_ids:
            return set()
        async with self._sessions() as session:
            return await BlockRepository(session).ai_turn_ids(turn_ids)

    async def stop_listening(self, timeout_s: float) -> None:
        """Hand every session this process listens to over to the next one.

        A message written into a running session is stamped consumed only when
        the session's receipt for it is read back; stop reading before that and
        the next turn sends it again. So the receipts already on their way are
        read first, for up to ``timeout_s``, and only then does this process
        stop reading. A receipt still missing by then is the ordinary case of a
        session that stopped reading, and replays like one.
        """
        deadline = time.monotonic() + timeout_s
        while any(self.live.unread_inputs.values()) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        # A replay still running reads its sessions too; the next process
        # replays them again from where this one landed.
        replays = list(self.live.replays.values())
        for replay in replays:
            replay.cancel()
        await asyncio.gather(*replays, return_exceptions=True)
        await self._compute.stop_listening()

    async def _set_hook_activity(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        active: bool,
        *,
        agent_handle: str | None = None,
    ) -> None:
        """Project subscription activity onto the existing realtime protocol."""
        del project_id

        # 「谁在干活」要和块署名答同一个名字：块落在 acting seat 上，所以
        # 帧也带它。轮次开账前（自起的轮次，账还没开）状态不在，退回运行时
        # 给的会话座位。
        state = self.live.hook_work.get((topic_id, work_id))
        agent = (state.acting_agent if state is not None else None) or agent_handle
        if active and state is None and await self._interval_closed(work_id):
            # A session still talking about a turn whose interval was closed
            # (the sweep stopped it, and said so): it is not running again.
            return
        if active:
            # A Working event is a session SPEAKING about a turn, not the step
            # that opened it: the mark belongs to that step, after its own await
            # commits the row (`steps/send.py`, `native_turns.begin`). Nor may
            # it drop a mark — `mark_turn_active`'s confirm can say no for a row
            # committed a moment ago, leaving a running turn unmarked.
            frame = {"type": "turn_started", "turn_id": str(work_id), "agent": agent}
        else:
            await self.live.mark_turn_inactive(topic_id, work_id)
            frame = {"type": "turn_finished", "turn_id": str(work_id), "agent": agent}
        await get_broker().publish(str(topic_id), frame)
        if not active:
            # The session went idle — the runner says so (a Claude Code session's
            # `SessionIdle`), or a harness that reports no session state ended its
            # work — so the agent has said what it understood: the moment to check
            # the name a task got from its opening line (room_task/naming.py).
            naming.nudge(topic_id, "idle")

            nudge_messages(self, topic_id)

    async def _interval_closed(self, work_id: uuid.UUID) -> bool:
        """Whether ``work_id`` has an interval and it is closed. A turn with
        no interval yet (a session starting one of its own) is not."""
        async with self._sessions() as session:
            row = await AgentTurnRepository(session).get(work_id)
            return row is not None and row.stopped_at is not None

    async def _note_reachability(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        reachable: bool,
        reason: str,
    ) -> None:
        """Say the turn is waiting for its machine (hook_stream.py)."""
        return await _note_reachability(
            self._sessions,
            self.live,
            project_id,
            topic_id,
            work_id,
            reachable,
            reason,
        )
