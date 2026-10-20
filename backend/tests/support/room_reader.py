"""A room's ear for tests that listen to its sessions by kind: each kind of item
a room's sessions hand the room (``room.reads.RoomReader``) goes to the
callable given for it, called with what that kind carries; any other kind goes
to ``rest``, when there is one, and is dropped otherwise."""

import uuid

from app.domain.agent.reads import (
    Abandoned,
    Followed,
    Reachable,
    Received,
    Replaced,
    Report,
    ResultLanded,
    Working,
    Writing,
)
from app.domain.agent.room.reads import RoomReader


def room_reader(
    *,
    events=None,
    activity=None,
    receipts=None,
    reports=None,
    abandoned=None,
    reachability=None,
    live=None,
    rest: RoomReader | None = None,
) -> RoomReader:
    """``events(project, topic, work, event, eid, text_seen, unsolicited)``,
    ``activity(project, topic, work, active, agent_handle=...)``,
    ``receipts(receipt)``, ``reports(report)``, ``abandoned(work)``,
    ``reachability(project, topic, work, reachable, reason)``,
    ``live(topic, work, author, blocks)``."""

    async def hear(session, read):
        event = read.event
        work = uuid.UUID(read.work_id) if read.work_id else None
        if isinstance(event, Received):
            heard = receipts and receipts(event.receipt)
        elif isinstance(event, Report):
            heard = reports and reports(event)
        elif isinstance(event, Abandoned):
            heard = abandoned and abandoned(work)
        elif isinstance(event, Working):
            heard = activity and activity(
                session.project_id,
                session.topic_id,
                work,
                event.active,
                agent_handle=session.agent_handle,
            )
        elif isinstance(event, Reachable):
            heard = reachability and reachability(
                session.project_id, session.topic_id, work, event.yes, event.reason
            )
        elif isinstance(event, Followed | Replaced | ResultLanded):
            # The room's own bookkeeping on a report or a replaced runner: no
            # kind of its own here; ``rest`` hears it when there is one.
            heard = None
        elif isinstance(event, Writing):
            heard = live and live(
                session.topic_id,
                work,
                event.author or session.agent_handle,
                list(event.blocks),
            )
        else:
            heard = events and events(
                session.project_id,
                session.topic_id,
                work,
                event,
                read.eid,
                read.text_seen,
                read.unsolicited,
            )
        # What the room answered: for a report, whether it was applied now.
        if heard:
            return await heard
        if heard is None and rest is not None:
            return await rest(session, read)
        return None

    return hear
