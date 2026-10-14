"""Per-process liveness and cache: what this process is working on right now,
keyed by room and by turn. Held and owned by ``ChatService`` (``ChatService.live``)
and read by the handlers that decide, persist and broadcast. None of it is a
source of truth: it lives and dies with the process, and the durable rows are
written elsewhere.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

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


@dataclass(frozen=True, slots=True)
class DurableTurnSeat:
    """A live turn known only from its durable row.

    What the process-local ``hook_work`` would have told a reader in one
    process — the seat the turn authors under and the seat whose session answers
    for it — reconstructed from the open ``AgentTurn`` row, so the cross-process
    half of 「这条消息该进哪一轮」 sees a turn this process never assembled.
    ``agent_handle`` is the one column the row holds for both questions; it is
    ``None`` on a row that reached the table before it was assembled.
    """

    agent_instance_handle: str | None
    acting_agent: str | None


class TurnSeat(Protocol):
    """What names whose turn it is: the two seats a live turn is read by.

    Both ``HookWorkState`` (this process's own work) and ``DurableTurnSeat`` (a
    row another process opened) carry these, and that is the whole reason the
    pickers below can ask one question of a mixed set — the durable row cannot
    offer more, and a caller aiming a steer or stamping ``seen_by`` needs only
    these two. Declared as a Protocol so the reader of a turn does not name
    either concrete class. Read-only members: a class's ``acting_agent: str``
    then satisfies it, which a writable ``str | None`` member would reject.
    """

    @property
    def agent_instance_handle(self) -> str | None: ...

    @property
    def acting_agent(self) -> str | None: ...


def pick_unambiguous_entry[StateT: TurnSeat](
    entries: Iterable[tuple[uuid.UUID, StateT | None]],
    matches: Callable[[StateT], bool] | None,
    *,
    strict: bool,
) -> tuple[uuid.UUID, StateT | None] | None:
    """The one live turn an inbound message belongs to, with the state naming it.

    With no ``matches``, a single live turn on the topic is the answer. With
    several (parallel seats in one room) only an exact ``matches`` hit decides,
    and only when exactly one hits: delivering to a guessed turn is worse than
    holding the message for none. A live id with no state cannot be ruled out —
    the old single-slot tolerance — unless ``strict``, which refuses to deliver
    without a state. Shared by the process-local set and the durable rows so the
    two answer the question the same way. Returns the state beside the id so a
    caller can aim the steer and name ``seen_by`` at the turn's own seat.

    Generic in the state so a caller keeping one concrete kind (the local
    ``HookWorkState``) reads that kind back, while the mixed authority set is
    typed as the ``TurnSeat`` they share.
    """
    entries = list(entries)
    if not entries:
        return None
    if matches is None:
        return entries[0] if len(entries) == 1 else None
    hits: list[tuple[uuid.UUID, StateT | None]] = []
    for work_id, state in entries:
        if state is None:
            if not strict:
                hits.append((work_id, None))
        elif matches(state):
            hits.append((work_id, state))
    return hits[0] if len(hits) == 1 else None


def pick_unambiguous[StateT: TurnSeat](
    entries: Iterable[tuple[uuid.UUID, StateT | None]],
    matches: Callable[[StateT], bool] | None,
    *,
    strict: bool,
) -> uuid.UUID | None:
    """The id alone — `pick_unambiguous_entry` without the state."""
    picked = pick_unambiguous_entry(entries, matches, strict=strict)
    return picked[0] if picked is not None else None


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
    # Same shape, other thing: when this turn was last called on to bring its own
    # checklist up to date. 一轮可以一直在说话、清单却停在开工那一版，而清单是看
    # 的人唯一能看见「做到哪了」的地方——`silence_reminder` 数不着它。这里是已提醒
    # 的时刻，不是清单本身的时刻：清单自己的时刻在 `TopicProgress.updated_at`，
    # 那才是它会因为一次写入而变新的地方。
    last_checklist_reminder_at: datetime | None = None
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
        cannot.
        """
        if self._confirm_closed is not None:
            try:
                closed = await self._confirm_closed(topic_id, work_id)
            except Exception:  # noqa: BLE001 — a failed confirm is not a close
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

    def consuming_work_id(
        self,
        topic_id: uuid.UUID,
        matches: Callable[[HookWorkState], bool] | None = None,
        *,
        strict: bool = False,
    ) -> uuid.UUID | None:
        """The live turn an inbound message belongs to, if unambiguous — the
        turns THIS process is holding. `authority.consuming_turn` adds the rows
        another process opened and asks the same question of both."""
        active = self.active_turn_ids.get(topic_id)
        if not active:
            return None
        entries = [
            (work_id, self.hook_work.get((topic_id, work_id))) for work_id in active
        ]
        return pick_unambiguous(entries, matches, strict=strict)

    def attributed_work_id(self, topic_id: uuid.UUID, author: str) -> uuid.UUID | None:
        """Which live hook work a room publication with no turn id belongs to."""
        live = [s for (t, _), s in self.hook_work.items() if t == topic_id]
        own = [s for s in live if s.acting_agent == author]
        if len(own) == 1:
            return own[0].work_id
        if len(own) > 1:
            active = self.active_turn_ids.get(topic_id, ())
            hits = [s.work_id for s in own if s.work_id in active]
            return hits[0] if len(hits) == 1 else None
        if len(live) == 1:
            return live[0].work_id
        return None
