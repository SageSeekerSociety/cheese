"""Telling a room that one of its panels changed, so it reads that panel again.

Sent by the handler that changed the resource, once the change is committed:
that handler is the one place that knows the change happened, whichever client
asked for it (the CLI, `cheese api`, the page itself).

It sits beside ``runtime`` rather than inside it — this publisher has one
reason to change, the frame it writes, while ``runtime`` runs the work — and
``runtime`` is over its size cap and may only shrink (see
``.claude/scripts/check-file-sizes.py``).
"""

import uuid

from app.domain.agent.runtime import Frame, get_broker


async def announce_stale(
    room_id: uuid.UUID, resource: str, *, id: uuid.UUID | None = None
) -> None:
    """Tell a room that one of its panels changed, so it reads that panel again.

    The frontend maps ``resource`` to the panel it reloads.

    ``id`` is the changed row, when the panel can read that one row back
    instead of the whole list. It is NOT the channel the frame is published on:
    for ``topics`` the row is a room, so a frame published on a task's
    conversation (its own page listens there) still names the ROOM it belongs
    to — a task id is not an address (`GET /topics/{task}` answers 404) and the
    sidebar holds no task rows. Callers that leave it out keep today's
    whole-list reload, which is also what a client that does not know the field
    does with the frame.
    """
    frame: Frame = {"type": "state", "resource": resource}
    if id is not None:
        frame["id"] = str(id)
    await get_broker().publish(str(room_id), frame)
