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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import (
    DateTime,
    String,
    Uuid,
    any_,
    bindparam,
    column,
    func,
    or_,
    select,
    table,
    true,
)
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
    threads_of_rooms,
)
from app.domain.identity.handles import agent_handle_column, recipient_seat
from app.domain.run_record.models import RunRecord

#: Only this far back. A wait is about someone waiting now; a message nobody
#: answered a week ago is not that any more, and without a bound these queries
#: scan every message the project ever had.
REPLY_LOOKBACK = timedelta(days=7)

#: How long an unrecovered failure may hold a line red before it stops counting
#: (`unresolved_failures`): three days. A dead branch — a 支线 whose turn blew up
#: and which nobody came back to — otherwise holds its whole channel red for
#: ever, since only a recovery ends a failure and silence is not one. Long
#: enough that an overnight machine being down is not a fault to retire; a
#: failure inside the window still counts, whatever else happens.
FAILURE_GIVES_UP_AFTER = timedelta(hours=72)

#: The reason a failed turn is reported under.
FAILED = "failed"

#: Run records on the machine side: a turn waiting for the machine it runs on.
MACHINE_RECORDS = ("device_waiting",)

# 「这个对话里的会话后来又读进了什么」：取 conversation_id 和 echoed_at 两列，一张
# 裸表。整张 `NativeInput` 属于 delivery 域，这里要的只是它被读到的那个瞬间 —— 用
# 裸表是为了不去 import 那个模型，block 域不必因此多一条跨域依赖。
_INPUTS = table(
    "native_inputs",
    column("conversation_id", Uuid),
    column("echoed_at", DateTime(timezone=True)),
)


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
        threads: Mapping[uuid.UUID, uuid.UUID] | None = None,
    ) -> dict[uuid.UUID, list[MemberWait]]:
        """{room: the members it is waiting on} — every room in one go.

        One wait per member and room. A failed turn outranks the rest (it is
        already known to be broken; the others are only taking long); among the
        rest the earliest wait decides, because whoever has waited longest
        decides when the mark comes on.

        ``threads``: 这些房间的支线，调用方已经问过的那一份（见
        `topics_signals.at_work`）。没给就自己问。
        """
        if not topic_ids:
            return {}
        since = now - REPLY_LOOKBACK
        spoke = await self._last_said(topic_ids, since, threads)
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

    async def unresolved_failures(
        self, units: Mapping[uuid.UUID, Sequence[uuid.UUID]]
    ) -> dict[uuid.UUID, datetime]:
        """{row: when the earliest failure still unresolved in it began}.

        一行 = 频道自己那条线，或一件活。`units` 把每一行映射到它由哪几段对话组成：
        频道是它自己那条线加上它的支线（调用方已经按「还在当支线用」筛过——原地转
        成任务的支线算另一件活，不折回频道），一件活就是它自己那条线。谁在哪段对话
        里开口，就只算那一行的恢复：按 `units` 折，不再自己查支线表。

        A failure is a platform event saying a turn ended in an error
        (`indexed_rows.FAILED_TURN_ROWS`, the same predicate the room-waits scan
        uses). It is unresolved until, after it, one of two things happens in the
        row:

        - the member whose turn it was speaks there again — a retry that worked,
          or the next answer coming normally; or
        - a session there reads an input (`native_inputs.echoed_at`): a message,
          an instruction, or the platform's own resume of the failed work. The
          row is being worked on again, and that is behind the failure whether
          or not the work it was read into succeeds (work that goes quiet, or
          fails in turn, is a *new* failure with its own clock, not the old one
          still counting).

        An input that was written to a session and never read clears nothing:
        nothing has heard it, so nothing is working on the row. Nor does a turn
        interval opening — the platform opening one says only that it meant to
        start work, not that any session took it up.

        Taking the earliest unresolved failure is what lets the sidebar count
        from the moment the row broke and stay red across a retry that failed
        again — while a recovery, silent or not, clears it. There is deliberately
        no time window here: recovery, not elapsed time, is what ends it, so a
        row that nobody has picked up stays red however long ago it broke.

        Row-level, not conversation-level: a channel is one row even though its
        line and its 支线 are separate conversations, so both are queried and
        the answer folded under the row id the caller named. The caller decides
        which rows to ask for — a channel that wants its tasks' failures too
        passes each task as its own row and takes the earliest.
        """
        conversations = [c for ids in units.values() for c in ids]
        if not conversations:
            return {}
        unit_of = {c: unit for unit, ids in units.items() for c in ids}
        failures = (
            await self._session.execute(
                select(Block.conversation_id, Block.turn_id, Block.created_at)
                .where(
                    _among(Block.conversation_id, conversations, Uuid),
                    ~participant_blocks(),
                    FAILED_TURN_ROWS,
                )
                .order_by(Block.created_at)
            )
        ).all()
        if not failures:
            return {}
        since = failures[0][2]
        owners = await self._turn_owners(
            conversations, since, {turn for _, turn, _ in failures if turn}
        )
        spoke = await self._last_said(conversations, since, unit_of)
        read = await self._inputs_read(conversations, unit_of, since)
        # 按 created_at 排好，所以每一行遇到的第一条失败就是它最早的那条；那一条没
        # 恢复就是答案，恢复了就继续往后看（它之后的下一条才是现在的这一条）。
        found: dict[uuid.UUID, datetime] = {}
        for conversation, turn, at in failures:
            unit = unit_of[conversation]
            if unit in found:
                continue
            member = owners.get(turn) if turn else None
            if self._answered(spoke, unit, member, at):
                continue
            newest_read = read.get(unit)
            if newest_read is not None and newest_read > at:
                continue
            found[unit] = at
        return found

    async def _inputs_read(
        self,
        conversation_ids: list[uuid.UUID],
        unit_of: Mapping[uuid.UUID, uuid.UUID],
        since: datetime,
    ) -> dict[uuid.UUID, datetime]:
        """{row: when a session there last read an input} since the earliest
        failure. Only the read counts (`echoed_at`): an input written and never
        read was heard by nobody."""
        stmt = (
            select(_INPUTS.c.conversation_id, func.max(_INPUTS.c.echoed_at))
            .where(
                _among(_INPUTS.c.conversation_id, conversation_ids, Uuid),
                _INPUTS.c.echoed_at >= since,
            )
            .group_by(_INPUTS.c.conversation_id)
        )
        newest: dict[uuid.UUID, datetime] = {}
        for conversation, at in (await self._session.execute(stmt)).all():
            unit = unit_of[conversation]
            if unit not in newest or at > newest[unit]:
                newest[unit] = at
        return newest

    async def _last_said(
        self,
        topic_ids: list[uuid.UUID],
        since: datetime,
        threads: Mapping[uuid.UUID, uuid.UUID] | None = None,
    ) -> dict[tuple[uuid.UUID, str], datetime]:
        """{(room, agent): when it last said something of the room's own} — the
        room's main line and its 支线.

        A 支线 is where the room is answered: a channel has no line of 芝士's
        own, so what it replies to a message in the channel's main line is said
        in that message's 支线 and nowhere else. A task's is not: a task is a
        board of its own with a card of its own, and what is said on one is not
        the room's own doings.
        """
        if threads is None:
            threads = await threads_of_rooms(self._session, topic_ids)
        # 筛选条件用字面量（`indexed_rows.LAST_SAID_ROWS`），和部分索引的谓词逐字
        # 相同 —— ORM 那版把 `kind`、`author_type`、署名前缀都绑成参数，缓存下来的
        # 通用计划看不见它们，就证不出这条查询蕴含索引的 WHERE，索引被绕过（实测）。
        # 支线顺着房间一起进同一个数组参数，不是另起一次查询也不是子查询：形状
        # 不变，索引才照样用得上（`_among`、`conversations_of`）。
        stmt = (
            select(Block.conversation_id, Block.author, Block.created_at)
            .where(
                _among(Block.conversation_id, [*topic_ids, *threads], Uuid),
                Block.created_at >= since,
                LAST_SAID_ROWS,
            )
            .order_by(Block.conversation_id, Block.author, Block.created_at.desc())
            .distinct(Block.conversation_id, Block.author)
        )
        said: dict[tuple[uuid.UUID, str], datetime] = {}
        for row in (await self._session.execute(stmt)).all():
            conversation, author, at = row[0], row[1], row[2]
            room: uuid.UUID | None = threads.get(conversation, conversation)
            if room is None or author is None:
                continue
            # 支线里的那句和主线上的那句归到同一个房间：同一个队友两处都说过时，留晚的
            # 那句 —— `_answered` 比的就是「这个人有没有比那条消息更晚说过话」。
            held = said.get((room, author))
            if held is None or at > held:
                said[(room, author)] = at
        return said

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
