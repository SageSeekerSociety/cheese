"""Which member a room is waiting on, and since when.

A room does not stall; a member does. Someone addressed a teammate and it has
not answered, a teammate's turn ended in an error and it has not spoken since,
or a card is stuck on something an agent has to fix and the agent that last
worked here has not touched it. Each of those is a wait on one member, so each
comes back attributed: `MemberWait.member` is that member's handle (an agent's
seat, the name its blocks are signed with), or None where the timeline does not
say whose it is.

How long counts as too long is the sidebar's call, against its own clock: this
only says who and since when. A member who is working in the room right now is
not waiting on anyone; that filter is the caller's, since activity lives in the
broker and not in the database.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import String, Uuid, any_, bindparam, or_, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.authorship import participant_blocks
from app.domain.block.indexed_rows import (
    AGENT_CHECK_ROWS,
    FAILED_TURN_ROWS,
    LAST_SAID_ROWS,
    MACHINE_EVENT_ROWS,
    UNANSWERED_ROWS,
)
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    PROMPTED_TURN_META_KEY,
    Block,
    BlockKind,
)
from app.domain.conversation.services import (
    conversations_of,
    of_rooms,
    room_column,
)
from app.domain.identity.handles import agent_handle_column, recipient_seat
from app.domain.run_record.models import RunRecord

#: Only this far back. A wait is about someone waiting now; a message nobody
#: answered a week ago is not that any more, and without a bound these queries
#: scan every message the project ever had.
REPLY_LOOKBACK = timedelta(days=7)

#: The reason a failed turn is reported under.
FAILED = "failed"

#: Run records on the machine side: a turn waiting for the machine it runs on.
MACHINE_RECORDS = ("device_waiting",)


def _among(column, values, item_type):
    """`column = ANY(:values)`, one array parameter however many values there are.

    An `IN` list binds each value on its own, so the SQL grows with the list
    and is compiled on the event loop at every call, then prepared afresh by
    asyncpg because its text changed. Rooms are counted in hundreds and a
    week's failed turns in thousands: about 100 ms of loop time per
    `GET /topics` on dev (2026-10-04), with every other request held behind it.
    Past 32767 values asyncpg refuses the query, and the page with it.
    """
    return column == any_(bindparam(None, list(values), type_=ARRAY(item_type)))


@dataclass(frozen=True)
class StuckCard:
    """A card in a room stuck on something an agent has to fix: which kind, which PR."""

    kind: str
    pr: int | None


@dataclass(frozen=True)
class MemberWait:
    """The room is waiting on `member`, since `since`, because of `reason`.

    `reason`: `failed` (its turn ended in an error); `mention` (a person
    addressed it); `check` / `conflict` / `rejected` / `gate` (a card is stuck
    on an agent fix, see `presentation.agent_fix_kind`); or the type of the
    newest machine event that landed during the wait
    (`indexed_rows.MACHINE_EVENTS`, `MACHINE_RECORDS`). `pr` is the stuck
    card's PR number.
    """

    member: str | None
    since: datetime
    reason: str
    pr: int | None = None


class MemberWaits:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def for_rooms(
        self,
        topic_ids: list[uuid.UUID],
        *,
        now: datetime,
        stuck_rooms: Mapping[uuid.UUID, StuckCard] | None = None,
    ) -> dict[uuid.UUID, list[MemberWait]]:
        """{room: the members it is waiting on} — every room in one go.

        One wait per member and room. A failed turn outranks the rest (it is
        already known to be broken; the others are only taking long); among the
        rest the earliest wait decides, because whoever has waited longest
        decides when the mark comes on.
        """
        if not topic_ids:
            return {}
        since = now - REPLY_LOOKBACK
        spoke = await self._last_said(topic_ids, since)
        waits: dict[tuple[uuid.UUID, str | None], MemberWait] = {}

        def keep(room: uuid.UUID, wait: MemberWait) -> None:
            held = waits.get((room, wait.member))
            if held is None or (held.reason != FAILED and wait.since < held.since):
                waits[(room, wait.member)] = wait

        for room, wait in await self._unanswered(topic_ids, since, spoke):
            keep(room, wait)
        stuck_rooms = stuck_rooms or {}
        stuck = [t for t in topic_ids if t in stuck_rooms]
        for room, wait in await self._stuck_cards(stuck, since, stuck_rooms):
            keep(room, wait)
        if waits:
            machine = await self._machine_events(
                list({room for room, _ in waits}), since, spoke
            )
            for key, wait in list(waits.items()):
                event = machine.get(key[0])
                # Only one that landed during the wait: a machine that was
                # already fine before it began has nothing to do with it.
                if event is not None and event[1] >= wait.since:
                    waits[key] = replace(wait, reason=event[0])
        for room, wait in await self._failed_turns(topic_ids, since, spoke):
            waits[(room, wait.member)] = wait
        out: dict[uuid.UUID, list[MemberWait]] = {}
        for (room, _), wait in sorted(waits.items(), key=lambda kv: kv[1].since):
            out.setdefault(room, []).append(wait)
        return out

    async def _last_said(
        self, topic_ids: list[uuid.UUID], since: datetime
    ) -> dict[tuple[uuid.UUID, str], datetime]:
        """{(room, agent): when it last said something on the room's own line}."""
        # 筛选条件用字面量（`indexed_rows.LAST_SAID_ROWS`），和部分索引的谓词逐字
        # 相同 —— ORM 那版把 `kind`、`author_type`、署名前缀都绑成参数，缓存下来的
        # 通用计划看不见它们，就证不出这条查询蕴含索引的 WHERE，索引被绕过（实测）。
        stmt = (
            select(Block.conversation_id, Block.author, Block.created_at)
            .where(
                _among(Block.conversation_id, topic_ids, Uuid),
                Block.created_at >= since,
                LAST_SAID_ROWS,
            )
            .order_by(Block.conversation_id, Block.author, Block.created_at.desc())
            .distinct(Block.conversation_id, Block.author)
        )
        return {
            (room, author): at
            for room, author, at in (await self._session.execute(stmt)).all()
        }

    @staticmethod
    def _answered(
        spoke: Mapping[tuple[uuid.UUID, str], datetime],
        room: uuid.UUID,
        member: str | None,
        at: datetime,
    ) -> bool:
        """Has `member` (any agent, when the member is unknown) spoken after `at`?"""
        if member is not None:
            said = spoke.get((room, member))
            return said is not None and said > at
        return any(said > at for (r, _), said in spoke.items() if r == room)

    async def _unanswered(self, topic_ids, since, spoke):
        """A person addressed an agent (`agent_recipient.mentioned`, the kind of
        message that starts a turn) and that agent has not spoken since.

        A message a finished turn took in counts as answered even if the agent
        chose to stay quiet (`consumed_turn`); a dead turn does not stamp it, so
        a stuck one is not swallowed. A platform notice is not the agent
        answering, and people talking to each other wake nobody.
        """
        # 同上：条件是字面量（`indexed_rows.UNANSWERED_ROWS`），`CAST(...)` 的形状也是
        # 索引谓词的那一份。
        stmt = select(Block.conversation_id, Block.created_at, Block.meta).where(
            _among(Block.conversation_id, topic_ids, Uuid),
            Block.created_at >= since,
            UNANSWERED_ROWS,
        )
        found = []
        for room, at, meta in (await self._session.execute(stmt)).all():
            member = recipient_seat((meta or {}).get("agent_recipient"))
            if not self._answered(spoke, room, member, at):
                wait = MemberWait(member=member, since=at, reason="mention")
                found.append((room, wait))
        return found

    async def _stuck_cards(self, topic_ids, since, stuck_rooms):
        """Rooms whose card is stuck on an agent fix (the caller decides which,
        by the board's rule): the agent that last worked here is the one awaited.

        Whether anyone picked it up is read off the card, not off whether an
        agent said something (an unrelated remark would count). The clock runs
        from the later of the newest event that handed it to an agent and that
        agent's last touch in the room — each tool call and line it writes
        lands a block — so the minute between two rounds of fixing does not
        read as "nobody has touched it since the rejection". Events mostly land
        on a task's card, so the room is read with its tasks.
        """
        if not topic_ids:
            return []
        room = room_column(Block.conversation_id).label("room")
        events = (
            select(room, Block.created_at)
            .where(
                of_rooms(Block.conversation_id, topic_ids),
                Block.created_at >= since,
                ~participant_blocks(),
                AGENT_CHECK_ROWS,
            )
            .order_by(room, Block.created_at.desc())
            .distinct(room)
        )
        # The newest agent line in each conversation, read from the end of its
        # run in `ix_blocks_conversation_created_at`, and the newest of those per
        # room picked here. An agent at work writes most of a busy room's week,
        # so reading all of it to keep one row per room read most of the table.
        conversations = conversations_of(topic_ids)
        newest = (
            select(Block.author, Block.created_at)
            .where(
                Block.conversation_id == conversations.c.id,
                Block.created_at >= since,
                participant_blocks(),
                agent_handle_column(Block.author),
            )
            .order_by(Block.created_at.desc())
            .limit(1)
            .lateral()
        )
        touched = select(
            conversations.c.room_id, newest.c.author, newest.c.created_at
        ).join(newest, true())
        last_touch: dict[uuid.UUID, tuple[str, datetime]] = {}
        for at_room, author, at in (await self._session.execute(touched)).all():
            held = last_touch.get(at_room)
            if held is None or at > held[1]:
                last_touch[at_room] = (author, at)
        found = []
        for room, at in (await self._session.execute(events)).all():
            member, touch = last_touch.get(room, (None, None))
            card = stuck_rooms[room]
            found.append(
                (
                    room,
                    MemberWait(
                        member=member,
                        since=max(at, touch) if touch else at,
                        reason=card.kind,
                        pr=card.pr,
                    ),
                )
            )
        return found

    async def _machine_events(self, topic_ids, since, spoke):
        """{room: (newest machine event, when)} since an agent last spoke there.

        Two places hold them: what the conversation was told (an environment
        repaired) and what was only recorded (a turn waiting for its machine)."""
        newest: dict[uuid.UUID, tuple[str, datetime]] = {}
        event = Block.meta["event_type"].as_string()
        room = room_column(Block.conversation_id).label("room")
        told = (
            select(room, event, Block.created_at)
            .where(
                of_rooms(Block.conversation_id, topic_ids),
                Block.created_at >= since,
                ~participant_blocks(),
                MACHINE_EVENT_ROWS,
            )
            .order_by(room, Block.created_at.desc())
            .distinct(room)
        )
        record_room = room_column(RunRecord.conversation_id).label("room")
        recorded = (
            select(record_room, RunRecord.kind, RunRecord.created_at)
            .where(
                of_rooms(RunRecord.conversation_id, topic_ids),
                RunRecord.created_at >= since,
                RunRecord.kind.in_(MACHINE_RECORDS),
            )
            .order_by(record_room, RunRecord.created_at.desc())
            .distinct(record_room)
        )
        for stmt in (told, recorded):
            for at_room, kind, at in (await self._session.execute(stmt)).all():
                held = newest.get(at_room)
                if held is None or at > held[1]:
                    newest[at_room] = (kind, at)
        return {
            at_room: found
            for at_room, found in newest.items()
            if not self._answered(spoke, at_room, None, found[1])
        }

    async def _failed_turns(self, topic_ids, since, spoke):
        """A turn that ended in an error ("this turn did not finish: …") whose
        member has not spoken since. Such a room is broken now, not after a
        threshold; the member speaking again (a retry that worked, the next turn
        answering normally) ends it. The member is whoever the turn was: the
        agent that wrote in it, or the one the message that started it was
        handed to."""
        stmt = (
            select(Block.conversation_id, Block.turn_id, Block.created_at)
            .where(
                _among(Block.conversation_id, topic_ids, Uuid),
                Block.created_at >= since,
                ~participant_blocks(),
                FAILED_TURN_ROWS,
            )
            .order_by(Block.created_at)
        )
        failures = (await self._session.execute(stmt)).all()
        owners = await self._turn_owners(
            topic_ids, since, {turn for _, turn, _ in failures if turn}
        )
        found: dict[tuple[uuid.UUID, str | None], MemberWait] = {}
        for room, turn, at in failures:
            member = owners.get(turn) if turn else None
            if not self._answered(spoke, room, member, at):
                wait = MemberWait(member=member, since=at, reason=FAILED)
                found[(room, member)] = wait
        return [(room, wait) for (room, _), wait in found.items()]

    async def _turn_owners(
        self, topic_ids: list[uuid.UUID], since: datetime, turns: set[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """{turn: the agent whose turn it was} — from what it wrote in it, or
        from the message the turn was started for."""
        if not turns:
            return {}
        owners: dict[uuid.UUID, str] = {}
        prompts = select(Block.meta).where(
            of_rooms(Block.conversation_id, topic_ids),
            Block.created_at >= since,
            Block.kind == BlockKind.message,
            or_(
                _among(
                    Block.meta[PROMPTED_TURN_META_KEY].as_string(),
                    map(str, turns),
                    String,
                ),
                _among(
                    Block.meta[CONSUMED_TURN_META_KEY].as_string(),
                    map(str, turns),
                    String,
                ),
            ),
        )
        for (meta,) in (await self._session.execute(prompts)).all():
            seat = recipient_seat((meta or {}).get("agent_recipient"))
            for key in (PROMPTED_TURN_META_KEY, CONSUMED_TURN_META_KEY):
                if seat and (meta or {}).get(key):
                    owners.setdefault(uuid.UUID(str(meta[key])), seat)
        wrote = (
            select(Block.turn_id, Block.author)
            .where(
                _among(Block.turn_id, turns, Uuid),
                participant_blocks(),
                agent_handle_column(Block.author),
            )
            .distinct()
        )
        for turn, author in (await self._session.execute(wrote)).all():
            owners[turn] = author
        return owners
