"""Per-process liveness and cache: what this process is working on right now,
keyed by room and by turn. Held and owned by ``ChatService`` (``ChatService.live``)
and read by the handlers that decide, persist and broadcast. None of it is a
source of truth: it lives and dies with the process, and the durable rows are
written elsewhere.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.memory.models import MemoryScope

#: 现场状态查表的键：一间房 + 这一轮。
TurnKey = tuple[uuid.UUID, uuid.UUID]


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
    # The model this turn's session was launched on, for the usage row a turn
    # with no reported usage still writes. "" where this process never
    # assembled a turn for the session (a screen recovered on the way up).
    model: str = ""
    assistant_count: int = 0
    last_chat_at: datetime | None = None
    # When this turn was last told it had gone quiet — NOT whether it has been.
    # A flag meant one reminder per silent stretch, so a turn that worked for
    # three hours without publishing was asked once, at the ten-minute mark, and
    # then left alone for the remaining two hours and fifty minutes. The room
    # showing nothing for that long is the complaint this reminder exists for.
    last_progress_reminder_at: datetime | None = None
    #: 这一轮每次工具调用落在哪个现场块上，按 harness 自己的调用 id。结果回来时要
    #: 写上输出、挂了要标红的就是那一块。只在内存里、只活这一轮：重启丢掉的只是几
    #: 截输出和几个红点，不是记录。
    steps: dict[str, uuid.UUID] = field(default_factory=dict)

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

    def __init__(self) -> None:
        # --- 房间 -----------------------------------------------------------
        # Room-level locks for the few operations that are nobody's turn:
        # swapping the room's environment mid-turn and memory consolidation on
        # the root topic.
        self.topic_locks: dict[uuid.UUID, asyncio.Lock] = {}
        # Prompt construction is serialized per (topic, agent) seat: two agents
        # addressed in one room run their turns in parallel, while one agent's
        # turns still queue.
        self.seat_locks: dict[tuple[uuid.UUID, str], asyncio.Lock] = {}
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

    def lock_for(self, topic_id: uuid.UUID) -> asyncio.Lock:
        """The room's lock for operations that are nobody's turn."""
        lock = self.topic_locks.get(topic_id)
        if lock is None:
            lock = asyncio.Lock()
            self.topic_locks[topic_id] = lock
        return lock

    def seat_lock_for(self, topic_id: uuid.UUID, agent_handle: str) -> asyncio.Lock:
        """The (topic, agent) seat's lock: one agent's turns queue, two seats
        in one room run side by side."""
        key = (topic_id, agent_handle)
        lock = self.seat_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.seat_locks[key] = lock
        return lock

    def mark_turn_active(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> None:
        self.active_turn_ids.setdefault(topic_id, set()).add(work_id)

    def mark_turn_inactive(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> None:
        active = self.active_turn_ids.get(topic_id)
        if active is None:
            return
        active.discard(work_id)
        if not active:
            self.active_turn_ids.pop(topic_id, None)

    def consuming_work_id(
        self,
        topic_id: uuid.UUID,
        matches: Callable[[HookWorkState], bool] | None = None,
        *,
        strict: bool = False,
    ) -> uuid.UUID | None:
        """The live turn an inbound message belongs to, if unambiguous.

        With no ``matches``, a single active turn on the topic is the answer.
        With several live turns (parallel seats in one room) only an exact
        ``matches`` hit decides, and only when exactly one hits: delivering to
        a guessed turn is worse than holding the message for none. A live id
        with no hook state cannot be ruled out — the old single-slot tolerance
        — unless the caller passes ``strict``, which refuses to deliver
        without a state.
        """
        active = self.active_turn_ids.get(topic_id)
        if not active:
            return None
        if matches is None:
            return next(iter(active)) if len(active) == 1 else None
        hits = []
        for work_id in active:
            state = self.hook_work.get((topic_id, work_id))
            if state is None:
                if not strict:
                    hits.append(work_id)
            elif matches(state):
                hits.append(work_id)
        return hits[0] if len(hits) == 1 else None
