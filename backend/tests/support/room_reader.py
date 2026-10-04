"""A room's ear for tests that listen to a runtime by kind: each kind of item a
runtime hands the room (``harness.RoomReader``) goes to the callable given for
it, called with what that kind carries; any other kind goes to ``rest``, when
there is one, and is dropped otherwise."""

import uuid

from app.domain.agent.harness import RoomReader
from app.domain.agent.reads import (
    Completed,
    Ended,
    Reachable,
    Received,
    Terminated,
    Working,
    Writing,
)


def room_reader(
    *,
    events=None,
    activity=None,
    receipts=None,
    completions=None,
    terminations=None,
    reachability=None,
    live=None,
    rest: RoomReader | None = None,
) -> RoomReader:
    """``events(project, topic, work, event, eid, text_seen, unsolicited)``,
    ``activity(project, topic, work, active, agent_handle=...)``,
    ``receipts(receipt)``, ``completions(completion)``,
    ``terminations(termination)``,
    ``reachability(project, topic, work, reachable, reason)``,
    ``live(topic, work, author, blocks)``."""

    async def hear(session, read) -> None:
        event = read.event
        work = uuid.UUID(read.work_id) if read.work_id else None
        if isinstance(event, Received):
            heard = receipts and receipts(event.receipt)
        elif isinstance(event, Completed):
            heard = completions and completions(event.completion)
        elif isinstance(event, Terminated):
            heard = terminations and terminations(event.termination)
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
        elif isinstance(event, Writing):
            heard = live and live(
                session.topic_id,
                work,
                event.author or session.agent_handle,
                list(event.blocks),
            )
        elif isinstance(event, Ended):
            heard = None
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
        if heard:
            await heard
        elif heard is None and rest is not None:
            await rest(session, read)

    return hear
