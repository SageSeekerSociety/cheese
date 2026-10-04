"""Reading and writing the turn intervals in :mod:`app.domain.agent.models`."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.domain.agent.models import AgentTurn


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One open interval, as the orphan sweep reads it.

    A record, not the ORM row, because the sweep runs long enough to await
    several probes and post several events — holding a session open across all
    of that to keep an object attached would be a transaction that exists only
    to serve attribute access.
    """

    turn_id: uuid.UUID
    topic_id: uuid.UUID
    continuation_id: uuid.UUID
    author: str
    content: str
    is_resume: bool
    resendable: bool
    started_at: datetime
    delivered_at: datetime | None
    # The seat this turn ran in, None when it was never assembled.
    agent_handle: str | None = None
    # The conversation this turn ran in (FB-56 legacy③): the identity a
    # termination is matched by. None for rows written before it was
    # recorded — and those are not attributable to any conversation's death.
    session_id: str | None = None

    @property
    def delivered(self) -> bool:
        """Did the transport accept this turn's prompt? The sweep's whole
        attach-versus-re-send decision turns on this one fact."""
        return self.delivered_at is not None

    def age_s(self, now: datetime) -> float:
        return (now - self.started_at).total_seconds()


class AgentTurnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open(
        self,
        *,
        turn_id: uuid.UUID,
        topic_id: uuid.UUID,
        continuation_id: uuid.UUID,
        author: str,
        content: str,
        is_resume: bool,
        resendable: bool,
        started_at: datetime,
        delivered_at: datetime | None = None,
        agent_handle: str | None = None,
        session_id: str | None = None,
        exists_ok: bool = False,
    ) -> None:
        # `delivered_at` is for a turn that has no 投喂 phase to stamp later — it
        # is born delivered or it is born unclosable. Everything the platform
        # feeds leaves it None and stamps it when the transport accepts.
        if exists_ok:
            # A session's own work keeps its id across backend processes: the
            # row an earlier process opened for it is this row, and stays as
            # that process wrote it.
            await self._session.execute(
                insert(AgentTurn)
                .values(
                    id=turn_id,
                    topic_id=topic_id,
                    continuation_id=continuation_id,
                    author=author,
                    content=content,
                    is_resume=is_resume,
                    resendable=resendable,
                    started_at=started_at,
                    delivered_at=delivered_at,
                    agent_handle=agent_handle,
                    session_id=session_id,
                )
                .on_conflict_do_nothing(index_elements=[AgentTurn.id])
            )
            return
        self._session.add(
            AgentTurn(
                id=turn_id,
                topic_id=topic_id,
                continuation_id=continuation_id,
                author=author,
                content=content,
                is_resume=is_resume,
                resendable=resendable,
                started_at=started_at,
                delivered_at=delivered_at,
                session_id=session_id,
                agent_handle=agent_handle,
            )
        )

    async def mark_delivered(self, turn_id: uuid.UUID, at: datetime) -> None:
        """Stamp the moment the transport accepted this turn's write.

        Only the first one counts: a re-delivered prompt does not move the
        moment the session first heard the task.
        """
        await self._session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == turn_id, AgentTurn.delivered_at.is_(None))
            .values(delivered_at=at)
        )

    async def delivered(self, turn_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these turns had their prompt accepted by the transport."""
        ids = set(turn_ids)
        if not ids:
            return set()
        rows = await self._session.scalars(
            select(AgentTurn.id).where(
                AgentTurn.id.in_(ids), AgentTurn.delivered_at.is_not(None)
            )
        )
        return set(rows)

    async def note_context(
        self,
        turn_id: uuid.UUID,
        *,
        route: str,
        reply_to: uuid.UUID | None,
        agent_handle: str,
    ) -> None:
        """Record what ending this turn needs, for whichever backend ends it,
        and whose conversation it runs in."""
        await self._session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == turn_id)
            .values(route=route, reply_to=reply_to, agent_handle=agent_handle)
        )

    async def get(self, turn_id: uuid.UUID) -> AgentTurn | None:
        return await self._session.get(AgentTurn, turn_id)

    async def mark_credits_refused(self, turn_id: uuid.UUID, at: datetime) -> bool:
        """Stamp that admission refused this turn for spent credits (#715).

        First-writer-wins: admission is asked again for the SAME refusal (the
        proxy caches a verdict for 30s, Claude Code retries ten times), and only
        the call that actually flips the column should trigger the one-time room
        notice — which is exactly what the returned bool tells the caller. A
        second call for an already-stamped turn returns False and changes
        nothing.
        """
        result = await self._session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == turn_id, AgentTurn.credits_refused_at.is_(None))
            .values(credits_refused_at=at)
        )
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def credits_refused(self, turn_id: uuid.UUID) -> bool:
        """Was this turn ever stamped refused-for-credits? What the turn's own
        end (`StopFailure`) reads to decide whose wording the room gets."""
        value = (
            await self._session.execute(
                select(AgentTurn.credits_refused_at).where(AgentTurn.id == turn_id)
            )
        ).scalar_one_or_none()
        return value is not None

    async def open_turn_id_for_topic(self, topic_id: uuid.UUID) -> uuid.UUID | None:
        """The still-running turn at this PLACE, if there is one.

        Admission only ever has the place a caller claims to run in, never a
        turn id (a scoped token carries `t`, never `turn_id`) — this is how it
        finds the interval that place names, so a credits refusal can be
        stamped on the turn it actually refused.

        The room's OWN line, which is where every turn now runs; the rows a
        thread left behind before work stopped being a place are excluded by
        the same clause that used to select them.
        """
        stmt = (
            select(AgentTurn.id)
            .where(
                AgentTurn.topic_id == topic_id,
                AgentTurn.task_id.is_(None),
                AgentTurn.stopped_at.is_(None),
            )
            .order_by(AgentTurn.started_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def open_turn_author_for_topic(
        self, topic_id: uuid.UUID, *, agent_handle: str | None = None
    ) -> str | None:
        """这一轮由谁的消息发起 —— 也就是「这一轮的结果由谁在等」。

        芝士在轮次中途提出待确认问题，本轮就停在那里等回答。等的不是房间里任意一个
        人，而是把这件事交给它的那个人，而那条消息的作者正是 `AgentTurn.author`。

        平台发起的轮次（resume、各类提醒）作者是 `system`，那种轮次里的提问指不到
        具体的人 —— 这里照样把 `system` 返回，由调用点决定它意味着什么，和
        `open_turn_id_for_topic` 一样只回答被问到的那件事。

        ``agent_handle``：只看这位队友的那一轮。一个房间可以坐几位队友，各自
        在跑的轮次由不同的人发起。
        """
        stmt = (
            select(AgentTurn.author)
            .where(
                AgentTurn.topic_id == topic_id,
                AgentTurn.task_id.is_(None),
                AgentTurn.stopped_at.is_(None),
            )
            .order_by(AgentTurn.started_at.desc())
            .limit(1)
        )
        if agent_handle is not None:
            stmt = stmt.where(AgentTurn.agent_handle == agent_handle)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def interval_is_over(
        *,
        turn_id: InstrumentedAttribute[uuid.UUID],
        topic_id: InstrumentedAttribute[uuid.UUID],
    ) -> ColumnElement[bool]:
        """Whether the interval these columns name is one the platform ended.

        A correlated EXISTS, for rows that name their work from the outside: a
        delivery input carries the ``work_id``/``topic_id`` of the turn opened
        to carry it, and the platform ends work by stamping ``stopped_at``. A
        work the platform has no row for is NOT over — silence is not a
        conclusion. As a clause rather than a second question so the caller's
        query stays one query.
        """
        return (
            select(AgentTurn.id)
            .where(
                AgentTurn.id == turn_id,
                AgentTurn.topic_id == topic_id,
                AgentTurn.stopped_at.is_not(None),
            )
            .exists()
        )

    async def open_of(self, turn_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these intervals are still open. One query for however many
        are asked about — the board asks it for every row of a project at once.

        This is the durable half of 「这条活现在有没有人在做」: the id a piece of
        work filed when its worker started (`Task.execution_turn_id`), asked a
        second time after the process that watched it start has forgotten. The
        interval outlives all three ways that happens — a restart, a replaced
        room session, a subagent handing something back — and it is closed by
        the thing that knows the difference: the orphan sweep, on a dead
        container or a wedged turn. So "still open" is a fact somebody maintains,
        not an inference from silence, which is what lets the board answer
        「有人在做」 without trusting one process's memory.
        """
        ids = list(turn_ids)
        if not ids:
            return set()
        stmt = select(AgentTurn.id).where(
            AgentTurn.id.in_(ids), AgentTurn.stopped_at.is_(None)
        )
        return set((await self._session.execute(stmt)).scalars())

    async def close(self, turn_ids: Iterable[uuid.UUID], at: datetime) -> None:
        """End these intervals. Closing is not deleting — the ids stay readable
        next to the blocks that carry them."""
        ids = list(turn_ids)
        if not ids:
            return
        await self._session.execute(
            update(AgentTurn)
            .where(AgentTurn.id.in_(ids), AgentTurn.stopped_at.is_(None))
            .values(stopped_at=at)
        )

    async def still_open(
        self, topic_id: uuid.UUID, turn_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Which of ``turn_ids`` have no end yet."""
        if not turn_ids:
            return []
        return list(
            await self._session.scalars(
                select(AgentTurn.id).where(
                    AgentTurn.topic_id == topic_id,
                    AgentTurn.id.in_(turn_ids),
                    AgentTurn.stopped_at.is_(None),
                )
            )
        )

    async def close_one(
        self, topic_id: uuid.UUID, turn_id: uuid.UUID, at: datetime
    ) -> int:
        """End exactly one DELIVERED open interval, the one the Stop names (FB-56).

        The harness's Stop ends the session's current work, and the event
        carries that work's id end to end: the platform fed the session with
        ``work_id=turn_id`` and the session stamps it back. So the row the
        Stop may close is the one it names — never a neighbour's, a second
        teammate still working least of all.

        Delivered, because the interval is 投喂 → Stop and a turn that was never
        fed cannot be what this Stop is ending. A turn spends its first seconds
        (or minutes, if the box has to boot) between opening its interval and
        reaching the transport; a Stop from the previous conversation landing in
        that window would otherwise close it, and a turn with no open interval is
        invisible to every future sweep — the silent death this table exists to
        end. Its own coroutine closes it by id, delivered or not.

        An id that names no such row — stale, replayed, or simply not this
        room's — closes nothing: the owner cannot be located, and guessing a
        scope for it is how a teammate's turn dies.
        """
        result = await self._session.execute(
            update(AgentTurn)
            .where(
                AgentTurn.id == turn_id,
                AgentTurn.topic_id == topic_id,
                # The room's own line. A Stop is the room's session finishing,
                # and the intervals a thread left behind when work was still a
                # place are not this session's to close.
                AgentTurn.task_id.is_(None),
                AgentTurn.stopped_at.is_(None),
                AgentTurn.delivered_at.is_not(None),
            )
            .values(stopped_at=at)
        )
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def started_at_of(
        self, topic_id: uuid.UUID, turn_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """When each of these turns in `topic_id` started; a turn with no
        interval here is left out."""
        ids = set(turn_ids)
        if not ids:
            return {}
        rows = await self._session.execute(
            select(AgentTurn.id, AgentTurn.started_at).where(
                AgentTurn.topic_id == topic_id, AgentTurn.id.in_(ids)
            )
        )
        return {turn_id: _aware(started) for turn_id, started in rows}

    async def open_on(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None
    ) -> list[tuple[str, float, str | None]]:
        """The delivered turns still open on one line — the room's own
        (``task_id`` None) or one thread's: ``(turn id, started at as epoch
        seconds, agent seat)``. A turn not yet delivered has not reached its
        session, so nobody is working on it yet."""
        rows = await self._session.execute(
            select(AgentTurn.id, AgentTurn.started_at, AgentTurn.agent_handle).where(
                AgentTurn.topic_id == topic_id,
                AgentTurn.task_id.is_(None)
                if task_id is None
                else AgentTurn.task_id == task_id,
                AgentTurn.stopped_at.is_(None),
                AgentTurn.delivered_at.is_not(None),
            )
        )
        return [
            (str(turn_id), _aware(started).timestamp(), agent)
            for turn_id, started, agent in rows
        ]

    async def open_turns(self) -> list[TurnRecord]:
        """Every interval still open, oldest first."""
        rows = (
            await self._session.execute(
                select(AgentTurn)
                .where(AgentTurn.stopped_at.is_(None))
                .order_by(AgentTurn.started_at)
            )
        ).scalars()
        return [
            TurnRecord(
                turn_id=row.id,
                # The PLACE this turn ran in — the thread when it had one. The
                # sweep re-addresses work by this id, and re-addressing a
                # thread's turn to its room would resume the wrong conversation.
                topic_id=row.task_id or row.topic_id,
                continuation_id=row.continuation_id,
                author=row.author,
                content=row.content,
                is_resume=row.is_resume,
                resendable=row.resendable,
                started_at=_aware(row.started_at),
                delivered_at=(
                    None if row.delivered_at is None else _aware(row.delivered_at)
                ),
                session_id=row.session_id,
                agent_handle=row.agent_handle,
            )
            for row in rows
        ]


def _aware(value: datetime) -> datetime:
    """Postgres hands back tz-aware values; a driver that does not must not make
    every comparison in the sweep raise."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
