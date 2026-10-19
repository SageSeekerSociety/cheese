"""Letting a seat go, and finding it again.

``close`` lands what a seat's session wrote and stops keeping track of it;
``stop_listening`` does that for every seat and leaves every session running;
``_detach`` is the bookkeeping both share. ``recover`` is the other direction:
after a restart, the placements a channel still knows about are asked once each,
and the ones still alive are adopted rather than started — what they said while
nobody read them is ``replay``'s, which reads the tail and goes on reading.

Adopt-before-start is why ``recover`` lives beside ``close`` and not beside
``starting.ensure``: what outlives this process is the session host's, and this
module is the one the room keeps on the sessions that do.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

from app.domain.agent import attachments
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.subscription import Seat
from app.domain.agent.place import seat_key
from app.domain.agent.room import sessions as pkg
from app.domain.agent.room.sessions.records import Clock, Live, _home
from app.domain.agent.session_host.contract import Access, Owner
from app.domain.agent.session_host.contract import SessionRef as CoreRef
from app.domain.agent.session_host.host import RunnerUnsupported

logger = logging.getLogger("app.domain.agent.room.sessions")


async def _detach(svc: pkg.RoomSessions, seat: Seat) -> None:
    task = svc.tasks.pop(seat, None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with attachments.lock(seat):
        if svc.live.pop(seat, None) is not None:
            attachments.note(seat, None)
    svc.work.pop(seat, None)
    svc.clocks.pop(seat, None)
    svc.told_waiting.pop(seat, None)
    svc.caught.pop(seat, None)


async def close(svc: pkg.RoomSessions, session: SessionRef) -> None:
    """Let this seat's session go: land what it has written, stop reading
    it and stop keeping track of it. Whatever a runner that is not there
    left is read when its session is next started."""
    seat = svc._seat_of(session)
    live = svc.live.get(seat)
    reading = svc.tasks.get(seat)
    if live is not None and reading is not None and not reading.done():
        # The pass under way finishes, and the reading ends with it.
        await svc.host.stop_listening([live.ref])
        await asyncio.wait({reading}, timeout=5)
    await svc._detach(seat)
    if live is not None:
        svc.host.release(live.ref)


async def stop_listening(svc: pkg.RoomSessions) -> None:
    """Stop reading every seat, and leave every session running.

    A pass already under way finishes first. The landing cursor only moves
    past what was taken, so whatever this process had not landed is still
    unread for the process that listens next.

    The swap is per seat and by INSTANCE (FB-56): each seat's registry note
    and map removal happen under its own attachment lock, and only for the
    exact session this stop started with — an attach that won the seat in
    the meantime keeps its reading, its registry entry and every map,
    because the swap it made was complete. A seat whose reading was about to
    start finds nothing to read.
    """
    targets = [(seat, live, svc.tasks.get(seat)) for seat, live in svc.live.items()]
    for seat, live, _task in targets:
        async with attachments.lock(seat):
            # Identity, not position: clear this seat's CURRENT state only
            # while it is still the session this stop took. A newer attach
            # is a complete swap and owns everything from here on.
            if svc.live.get(seat) is live:
                attachments.note(seat, None)
                for held in (
                    svc.live,
                    svc.tasks,
                    svc.work,
                    svc.clocks,
                    svc.told_waiting,
                ):
                    held.pop(seat, None)
    await svc.host.stop_listening([live.ref for _, live, _ in targets])
    pending = [task for _, _, task in targets if task is not None and not task.done()]
    if pending:
        _, stuck = await asyncio.wait(pending, timeout=5)
        for task in stuck:
            task.cancel()
        await asyncio.gather(*stuck, return_exceptions=True)


async def recover(
    svc: pkg.RoomSessions, device_id: str | None = None
) -> list[SessionRef]:
    """This pool's sessions of this harness that outlived the process
    before this one, read again. What they said while nobody read them is
    ``replay``'s.

    Each placed session's runner is asked once. An answer that its harness
    process is gone counts as the conversation's death only when it names
    the stored resume token (FB-56 legacy③); a runner that could not be
    asked is unknown, never dead.

    A runner the machine says is gone is recorded on its placement, and no
    later restart asks again: its death is the stored answer, until the
    next message starts the session and places it afresh. Every deploy
    restarts the backend, and asked each time, the same few hundred gone
    runners made a burst of failed calls on every one.
    """
    # These answer for THIS round only — a conversation the last round
    # reached says nothing about this one. (The cumulative witness is
    # `dead`: a death once seen stays seen.)
    svc.found_conversations.clear()
    svc.terminal_conversations.clear()
    asked_at = datetime.now(UTC)
    placements = await svc.channel.placed(svc.harness, device_id)
    for placed in placements:
        if placed.let_go and placed.resume_token:
            seat = svc._seat_of(placed.session)
            svc.terminal_conversations.add((seat, placed.resume_token))
    found: list[tuple[SessionRef, CoreRef, Access, str | None, str]] = [
        (
            placed.session,
            CoreRef(svc.harness, _home(placed.state)),
            Access(
                "",
                host=placed.machine,
                owner=Owner(
                    placed.session.project_id,
                    placed.session.conversation_id,
                    placed.resource_id,
                    placed.session.agent_handle,
                    placed.agent_handle,
                    # The seat it was started on: a task's or a 支线's own.
                    seat=seat_key(placed.agent_handle, placed.session.inner_id),
                ),
            ),
            placed.resume_token,
            placed.agent_handle,
        )
        for placed in placements
        if not placed.let_go
    ]
    await svc.host.adopt([(ref, access) for _, ref, access, _, _ in found])
    recovered: list[SessionRef] = []
    gone: list[SessionRef] = []
    for session, ref, access, resume_token, acting in found:
        seat = svc._seat_of(session)
        room_id = session.topic_id
        try:
            status = await svc.host.attach(ref, access)
        except RunnerUnsupported as exc:
            logger.warning("%s recovery refused topic=%s: %s", svc.label, room_id, exc)
            continue
        if status is None:
            continue
        if not status.alive:
            if status.runner_gone:
                gone.append(session)
            # A runner that is gone ran the placement's stored
            # conversation; one that answered names its own.
            if resume_token and (
                status.runner_gone or status.conversation == resume_token
            ):
                svc.terminal_conversations.add((seat, resume_token))
            continue
        svc.found_conversations.add((seat, status.conversation))
        try:
            await svc._attach(
                Live(
                    session,
                    ref,
                    acting,
                    status.conversation,
                    status.takes_inputs,
                )
            )
        except DeviceOffline:
            continue
        if status.working and status.work_id:
            svc.work[seat] = uuid.UUID(status.work_id)
            now = time.monotonic()
            svc.clocks[seat] = Clock(opened=now, progressed=now)
        svc.unchecked.add(seat)
        recovered.append(session)
    if gone:
        await svc.channel.let_go(gone, placed_before=asked_at)
    # Chat restores room bookkeeping before replay starts reading.
    return recovered


async def replay(svc: pkg.RoomSessions, session: SessionRef) -> None:
    """Land the tail a recovered session wrote while nobody read it, and go
    on reading it. Returns once the tail is landed; raises what stopped the
    reading before it was."""
    seat = svc._seat_of(session)
    if seat not in svc.live:
        return
    svc._listen(seat)
    reading = svc.tasks[seat]
    await svc.caught[seat].wait()
    failure = (
        reading.exception() if reading.done() and not reading.cancelled() else None
    )
    if failure is not None:
        # What the journal kept could not be settled (a retained result
        # that disagrees with its inputs): the seat is not read on, and
        # whoever recovered it hears why.
        raise failure
