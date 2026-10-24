"""Per-process liveness and cache: what this process is working on right now,
keyed by room and by turn. Held and owned by ``ChatService`` (``ChatService.live``)
and read by the handlers that decide, persist and broadcast. None of it is a
source of truth: it lives and dies with the process, and the durable rows are
written elsewhere. The seat and room leases are the one thing here whose
subject outlives the process — the cache of them dies with it, the seat they
hand out is held in Valkey until the turn lets it go.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.domain.agent.admission import Lease
from app.domain.memory.scopes import MemoryScope

logger = logging.getLogger(__name__)

#: 现场状态查表的键：一间房 + 这一轮。
TurnKey = tuple[uuid.UUID, uuid.UUID]

#: 写穿回调：确认库里那行开着的 `AgentTurn` 就是这一轮（`turn/state/authority.py`
#: 的 `confirm_open`：只读、不发明一行）。签名的两个参数就是标记自己的那两个
#: （topic_id、work_id）——写穿不许自己另发明一套键。它答的是「行在不在」：
#: 说不在（行还没提交、或已被清场）就不许把这一轮标进缓存。
WriteThrough = Callable[[uuid.UUID, uuid.UUID], Awaitable[bool]]

#: 松手回调：确认生命周期已经把这一轮收场了（`authority.confirm_closed`）。只在
#: mark_turn_inactive 时问一次——缓存松手而库里的行还开着，是缓存唯一不该替它遮
#: 掩的事，但收场仍归生命周期，这里不代它关。
ConfirmClosed = Callable[[uuid.UUID, uuid.UUID], Awaitable[bool]]


@dataclass
class HookWorkState:
    """Persistence context for work whose events arrive on a subscription."""

    project_id: uuid.UUID
    topic_id: uuid.UUID
    work_id: uuid.UUID
    pending_ids: set[uuid.UUID]
    reply_to: uuid.UUID | None
    # None where no prompt was assembled to read one — `_persist_assistant_message`
    # then loads it, which is NOT the same as passing []: [] means 私聊 (no member
    # list at all), and conflating the two flags every @ as a non-member.
    roster: list[dict] | None
    topic_refs: list[dict]
    continuation_id: uuid.UUID | None
    route: str
    acting_agent: str
    # Attribution and memory part ways here, deliberately: `acting_agent` is
    # the seat of the agent that ran this turn (who did it), while the pool
    # belongs to that agent across rooms (whose memory it is). Resolved at turn
    # start and carried, because the hook path reaches turn end with no session
    # left open to ask.
    agent_pool: tuple[MemoryScope, str] | None
    user_text: str
    started_at: datetime
    agent_instance_handle: str | None = None
    # Where this seat's reading stands once this turn finishes cleanly. The
    # blocks this turn answers for are `pending_ids`; the window also carried
    # other seats' messages, which are read but not stamped. None when this
    # process assembled no prompt (a session recovered on the way up).
    read_through: tuple[datetime, uuid.UUID] | None = None
    # The model this turn's session was launched on, for the usage row a turn
    # with no reported usage still writes. "" where this process never
    # assembled a turn for the session (a screen recovered on the way up).
    model: str = ""
    assistant_count: int = 0

    # The topic branch's commits as of turn start — what makes "this turn's
    # changes" answerable at turn end. A task rather than a value, because the
    # read shells out to git and creates the repo on first use; see where it is
    # started. `None` (or a read that failed) means the turn lands NO change
    # summary rather than a wrong one: with no baseline, every commit looks new.
    known_commits: asyncio.Task[set[str] | None] | None = None
    # Did the SESSION open this work rather than the platform? Then its
    # bookkeeping has no coroutine to fall out of, and turn end is the only
    # place the marks it left in the runner can be dropped.
    self_started: bool = False


class LiveWork:
    """This process's per-room, per-turn state.

    One per process (``ChatService.live``). The tables are public — handlers
    read and write a cell like ``live.hook_work`` directly — and the methods
    are the pure keying operations (which lock a seat shares, whether a turn
    is active, which live turn owns an inbound message) whose rules no single
    handler owns.
    """

    def __init__(
        self,
        *,
        write_open: WriteThrough | None = None,
        confirm_closed: ConfirmClosed | None = None,
    ) -> None:
        #: 写穿回调（`ChatService` 注入）：确认库里那行开着的 `AgentTurn` 就是
        #: 这一轮（`authority.confirm_open`，只读）。默认 None = 纯缓存（测试、
        #: 以及没有库的那条路）。见 `mark_turn_active` 为什么不在这里发明一行。
        self._write_open = write_open
        #: 松手回调（`ChatService` 注入）：确认生命周期已把这一轮收场
        #: （`authority.confirm_closed`，只读）。见 `mark_turn_inactive`：库里那行
        #: 还开着是缓存唯一不该替它遮掩的事，但收场仍归生命周期，这里不代它关。
        self._confirm_closed = confirm_closed
        # --- 房间 -----------------------------------------------------------
        # Room-level leases for the few operations that are nobody's turn:
        # swapping the room's environment mid-turn and memory consolidation on
        # the root topic.
        self.topic_locks: dict[uuid.UUID, Lease] = {}
        # Prompt construction is serialized per (topic, agent) seat: two agents
        # addressed in one room run their turns in parallel, while one agent's
        # turns still queue. The lease objects are this process's cache of them;
        # the seat itself is held in Valkey, so the next backend process queues
        # behind the same seat rather than starting a turn beside it.
        self.seat_locks: dict[tuple[uuid.UUID, str], Lease] = {}
        # 房间里此刻那个会话是哪位队友的（最近一次 AgentSessionInfo 说的）。
        self.room_session_agents: dict[uuid.UUID, str] = {}
        # Where each live session's model traffic goes, remembered from the last
        # turn the platform assembled for it. Insertion-ordered and trimmed from
        # the front: nothing tells this service a screen is gone, so without a
        # bound this is a dict that only ever grows. Keyed by topic (a
        # self-started turn has no prompt to resolve one from).
        self.session_route: dict[uuid.UUID, str] = {}
        # The model each session was launched on, beside its route and for the
        # same reason.
        self.session_model: dict[uuid.UUID, str] = {}
        # (room, inner, whether it is a 支线) per conversation: a task stays in
        # the room it hangs in until someone moves it, which drops this entry.
        self.conversation_rooms: dict[
            uuid.UUID, tuple[uuid.UUID, uuid.UUID | None, bool]
        ] = {}
        # Each room's replay of what its sessions said while nobody listened
        # (`recover_sessions`), for as long as it runs.
        self.replays: dict[uuid.UUID, asyncio.Task] = {}

        # --- 轮次 -----------------------------------------------------------
        # Work currently attributed to each active session. A SET per topic:
        # several seats can have a live turn in one room, so every reader
        # answers "is THIS turn among the live ones" rather than "is this THE
        # one".
        self.active_turn_ids: dict[uuid.UUID, set[uuid.UUID]] = {}
        # The live work each open turn is doing: the state its events are read
        # against. Keyed by (topic, work id).
        self.hook_work: dict[TurnKey, HookWorkState] = {}
        # The notice each open turn is keeping current: a streak of retries, a
        # wait for its machine, a compaction. One line per streak, restated as
        # it moves on. Keyed by turn id.
        self.retry_notes: dict[uuid.UUID, uuid.UUID] = {}
        self.waiting_notes: dict[uuid.UUID, uuid.UUID] = {}
        self.compact_notes: dict[uuid.UUID, uuid.UUID] = {}

        # --- 会话 / 对话 ----------------------------------------------------
        # Seats a reachable machine said are gone (recover_sessions), until a
        # session answers on them again (FB-56 legacy③).
        self.dead_sessions: set[tuple] = set()
        # Liveness only, keyed by conversation then durable input UUID.
        # Settlement never depends on this process cache.
        self.unread_inputs: dict[uuid.UUID, dict[uuid.UUID, float]] = {}

    def lock_for(self, topic_id: uuid.UUID) -> Lease:
        """The room's lease for operations that are nobody's turn."""
        lease = self.topic_locks.get(topic_id)
        if lease is None:
            lease = Lease(f"admission:room:{topic_id}")
            self.topic_locks[topic_id] = lease
        return lease

    def seat_lock_for(self, topic_id: uuid.UUID, agent_handle: str) -> Lease:
        """The (topic, agent) seat's lease: one agent's turns queue, two seats
        in one room run side by side."""
        key = (topic_id, agent_handle)
        lease = self.seat_locks.get(key)
        if lease is None:
            lease = Lease(f"admission:seat:{topic_id}:{agent_handle}")
            self.seat_locks[key] = lease
        return lease

    async def mark_turn_active(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> bool:
        """Mark a turn this process is holding as active, write-through.

        The cache is a memo; the durable `AgentTurn` row is the authority. The
        write-through (`write_open`) only CONFIRMS that row is open — it never
        inserts one, because the row is the turn lifecycle's to open with its
        real author and content (`authority.confirm_open`). The confirm runs
        FIRST, in the same awaited step as the mark, so a reader never sees the
        turn as running before the authority says its row is there: a confirm
        that raises, or that answers no (the lifecycle has not committed the row
        yet, or a sweep closed it), leaves no cache entry behind — and drops any
        stale one, because a closed or absent row must not be revived by the
        cache. Returns whether the turn is now marked in this process.
        """
        if self._write_open is not None:
            if not await self._write_open(topic_id, work_id):
                self.forget_active(topic_id, work_id)
                return False
        self.active_turn_ids.setdefault(topic_id, set()).add(work_id)
        return True

    def activate_cached(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> None:
        """Record a turn this process just saw committed, with no read.

        For a caller whose own awaited step committed the row — the turn
        lifecycle right after `open_turn` / `open_interval`. There the write the
        mark mirrors is ALREADY durable, so confirming it AGAIN is the one read
        that cannot be trusted: a fresh connection can see the row as not yet
        open at the commit boundary and answer no, which would drop a mark that
        belongs on. Marking on the caller's word is the point.
        """
        self.active_turn_ids.setdefault(topic_id, set()).add(work_id)

    async def mark_turn_inactive(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> None:
        """Let go of a turn this process was holding.

        Only the cache moves. Closing the row is the turn's own lifecycle's
        (`close_turns` at its end, `close_one` on its Stop), and closing it from
        here would be the one thing the design forbids: ending a real turn early
        on the say-so of a cache that is not the authority. But the cache must
        not paper over a row still open either, so this CONFIRMS the lifecycle
        ended it (`confirm_closed`) before letting go, and logs the one case it
        cannot. Letting go is unconditional: a confirm that is cancelled (a
        shutdown, a deadline) leaves the row exactly as it was — this never
        closes it — while the mark this process was holding still goes. Keeping
        the mark would be the worse of the two, since it claims a turn this
        process is no longer running.
        """
        try:
            if self._confirm_closed is not None:
                try:
                    closed = await self._confirm_closed(topic_id, work_id)
                except Exception:  # a failed confirm is not a close
                    logger.exception(
                        "could not confirm turn %s closed (topic=%s)", work_id, topic_id
                    )
                else:
                    if not closed:
                        logger.warning(
                            "letting go of turn %s while its row is still open "
                            "(topic=%s): end it via the turn lifecycle",
                            work_id,
                            topic_id,
                        )
        finally:
            self.forget_active(topic_id, work_id)

    def forget_active(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> None:
        """Drop the cache mark with no durable hop.

        For a caller that cannot await and whose row is already closed — the
        orphan sweep let go of a turn it ended, so there is nothing to write
        (`retire_unheard`).
        """
        active = self.active_turn_ids.get(topic_id)
        if active is None:
            return
        active.discard(work_id)
        if not active:
            self.active_turn_ids.pop(topic_id, None)
