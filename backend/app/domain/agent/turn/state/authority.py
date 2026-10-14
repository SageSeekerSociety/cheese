"""「这一轮在跑」的权威那一半：库里那行开着的 `AgentTurn`。

`LiveWork`（`live.py`）是这台进程的备忘录，读不到就回这里。这一层只读、只确认，
**不发明一行**：行的开场与收场都是轮次自己的生命周期（`turn/store/intervals.py`
的 `open_turn` / `close_turns`）提交的，这里去写一条等于替没人说过话的轮次编一个
作者（`_prompt_lock` 那条路的注释说了为什么）。一处例外也没有，所以这层没有
insert、没有 update，只有 select——对照写在这里，免得后来者把「写穿」误当成
「这里负责写」。

跨进程认出这一轮，靠的也正是这些读：另一条进程开的行，本进程的缓存里没有，读它
就能看见（`has_running_turn` / `consuming_turn`）。
"""

import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.turn.state.live import (
    DurableTurnSeat,
    LiveWork,
    pick_unambiguous_entry,
)

Sessions = async_sessionmaker[AsyncSession]


async def open_turn_seats(
    sessions: Sessions, topic_id: uuid.UUID
) -> dict[uuid.UUID, set[str | None]]:
    """The seats with an open interval here, read from the authority."""
    async with sessions() as session:
        return await AgentTurnRepository(session).open_turn_seats([topic_id])


async def has_running_turn(
    sessions: Sessions, topic_id: uuid.UUID, agent_handle: str | None = None
) -> bool:
    """Whether a turn is running in the topic — durably, across processes.

    The authority is the open ``AgentTurn`` row, not any process's cache: a
    teammate running the turn from another backend opened the same row, and a
    message landing here has to see it. A closed row is closed, and a stale
    cache entry must not keep a finished turn looking live.

    With ``agent_handle``, work of that agent's: teammates in one room run side
    by side, so another seat working leaves this one free. A turn whose agent is
    not known yet counts as anybody's.
    """
    handles = (await open_turn_seats(sessions, topic_id)).get(topic_id)
    if not handles:
        return False
    if agent_handle is None:
        return True
    return None in handles or agent_handle in handles


async def consuming_turn(
    live: LiveWork,
    sessions: Sessions,
    topic_id: uuid.UUID,
    matches: Callable[[object], bool] | None = None,
    *,
    strict: bool = False,
) -> tuple[uuid.UUID, object | None] | None:
    """The one live turn an inbound message belongs to, and the state that says
    whose it is: this process's own AND the rows another process opened.

    The union is asked the same question `LiveWork.consuming_work_id` asks of
    one process alone, so a turn running on another backend consumes the message
    instead of a second turn being opened beside it. A row this process also
    holds keeps its richer local state; a row known only durably answers with
    its seat (`DurableTurnSeat`) — that half is what aims the steer and names the
    input's ``seen_by`` when the turn runs elsewhere. A closed row is absent: the
    cache cannot nominate a turn the authority has ended.
    """
    local = live.active_turn_ids.get(topic_id) or set()
    async with sessions() as session:
        records = await AgentTurnRepository(session).open_turn_records(topic_id)
    entries: dict[uuid.UUID, object | None] = {
        work_id: live.hook_work.get((topic_id, work_id)) for work_id in local
    }
    for turn_id, handle in records:
        entries.setdefault(
            turn_id,
            DurableTurnSeat(agent_instance_handle=handle, acting_agent=handle),
        )
    return pick_unambiguous_entry(entries.items(), matches, strict=strict)


async def confirm_open(sessions: Sessions, work_id: uuid.UUID) -> bool:
    """Confirm the authority has this turn open; never create a row.

    The row is the turn lifecycle's to open. At ``_prompt_lock`` it does not
    exist yet, so there is nothing here to write and nothing to invent — the
    lifecycle commits the real row (author, content, continuation) a moment
    later, and this mark is only the in-process reservation until then. A row a
    sweep closed is left closed. Returns whether the row is open.
    """
    async with sessions() as session:
        return await AgentTurnRepository(session).turn_is_open(work_id)


async def confirm_closed(sessions: Sessions, work_id: uuid.UUID) -> bool:
    """Confirm the lifecycle has ended this turn before the cache lets go.

    Closing is the lifecycle's (`close_turns` / `close_one`); this only reads.
    A row still open here is the one thing the cache must not paper over — the
    caller logs it rather than closing a turn the authority still has running.
    """
    async with sessions() as session:
        return not await AgentTurnRepository(session).turn_is_open(work_id)
