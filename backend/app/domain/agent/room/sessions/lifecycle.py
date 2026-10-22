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

A runner another release started speaks a lifecycle protocol this one does not
read, and is replaced by one of this release's on the same conversation. It is
drained first, the way long-running workers are upgraded (a self-hosted GitHub
Actions runner updates itself when it is assigned a job, ``kubectl drain``
cordons a node and evicts its pods within their termination grace period, an
old Temporal worker version drains the workflows pinned to it): an
idle one is replaced at once, and one at work is given nothing new and left to
finish, while the seat's messages wait (``draining``), and is replaced once its
ping says it is idle. Its work still lands, because what an agent does reaches
the room through the platform's tools, not through the records this release
cannot read. Past ``settings.runner_drain_s`` its work is interrupted and
resumed on the new runner (``runner_replaced``).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

from app.core.config import settings
from app.domain.agent import attachments
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.subscription import Seat
from app.domain.agent.place import seat_key
from app.domain.agent.platform_failures import (
    RUNNER_REPLACED_CODE,
    runner_replaced_message,
)
from app.domain.agent.reads import Read, Replaced
from app.domain.agent.room import sessions as pkg
from app.domain.agent.room.sessions.records import Clock, Live, Outdated, _home
from app.domain.agent.service import AgentResult
from app.domain.agent.session_host.contract import Access, Ending, Owner
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
    # A drain is this process's watch on a runner it does not read; the process
    # that listens next finds the runner again and watches it from there.
    drains = list(svc.drains.values())
    svc.drains.clear()
    for drain in drains:
        drain.cancel()
    await asyncio.gather(*drains, return_exceptions=True)
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
        if status.outdated:
            # Its runner is another release's (``host.attach``): the
            # conversation goes on in a runner this release starts, once the
            # old one is idle (``replay``). Work it is doing stays the seat's
            # open work, so the room keeps the turn open meanwhile.
            work = (
                uuid.UUID(status.work_id) if status.working and status.work_id else None
            )
            svc.outdated[seat] = Outdated(
                Live(session, ref, acting, status.conversation),
                access,
                working=status.working,
                work=work,
                epoch=status.epoch,
                launch=status.launch or status.conversation,
                endings=status.endings,
            )
            if work is not None:
                svc.work[seat] = work
            recovered.append(session)
            continue
        try:
            await svc._attach(Live(session, ref, acting, status.conversation))
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
    reading before it was.

    A seat whose runner is another release's has no tail this release can
    read: an idle one is replaced now, one at work is drained (``_drain``)."""
    seat = svc._seat_of(session)
    if (outdated := svc.outdated.get(seat)) is not None:
        if outdated.working and not outdated.settling:
            if seat not in svc.drains:
                svc.drains[seat] = asyncio.create_task(
                    _drain(svc, seat, outdated),
                    name=f"drain:{seat[0]}/{seat[1]}",
                )
            return
        # A settlement that fails raises, and the replay is retried
        # (``recovery``) with the seat still owed.
        await _replaced(svc, outdated, failed=False)
        logger.info(
            "runner_replaced topic=%s seat=%s conversation=%s reason=idle",
            seat[0],
            seat[1],
            outdated.live.conversation,
        )
        return
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


async def _drain(svc: pkg.RoomSessions, seat: Seat, outdated: Outdated) -> None:
    """Let the work an outdated runner is doing finish, then replace it.

    Its ping is the one thing every protocol keeps (``lifecycle``'s
    handshake), so that is all this asks it, every ``DRAIN_POLL_S``. Idle, it
    is replaced, and its work ends the way its ping's ``endings`` say it did:
    as finished, or, when it ended in a failure, started again on the new
    runner (``runner_replaced``). Gone, its work is started again too. Still
    at work ``settings.runner_drain_s`` after the drain began (a moment kept
    with the seat, so a backend taking over counts from it too), its work is
    interrupted, the drain waits up to ``DRAIN_STOP_WAIT_S`` for the runner to
    say how that ended, and the work is started again, the way every
    replacement went before there was a drain. A runner that cannot be asked
    is waited for to the same cap. Work somebody stopped meanwhile
    (``RoomSessions.interrupt``) ends as stopped."""
    live = outdated.live
    began: datetime | None = None
    logger.info(
        "runner_drain_started topic=%s seat=%s conversation=%s work=%s cap_s=%.0f",
        seat[0],
        seat[1],
        live.conversation,
        outdated.work,
        settings.runner_drain_s,
    )

    def drained_for() -> float:
        return (datetime.now(UTC) - began).total_seconds() if began else 0.0

    reason = ""
    interrupted_at: datetime | None = None
    while True:
        try:
            if began is None:
                began = await svc.channel.drain_started(live.session, outdated.launch)
            status = await svc.host.attach(live.ref, outdated.access)
        except Exception:  # a drain that died would hold the seat's messages for good
            logger.exception(
                "outdated runner not asked topic=%s seat=%s", seat[0], seat[1]
            )
            status = None
        if status is not None and status.endings:
            outdated.endings = status.endings
        if status is not None and not status.alive:
            reason = reason or "gone"
            break
        if status is not None and not status.working:
            if not reason:
                failed = any(e.failed for e in _own(outdated))
                reason = "failed" if failed else "drained"
            break
        if interrupted_at is not None:
            if (datetime.now(UTC) - interrupted_at).total_seconds() >= (
                pkg.DRAIN_STOP_WAIT_S
            ):
                break
        elif began is not None and drained_for() >= settings.runner_drain_s:
            try:
                interrupted = await svc.host.stop(
                    live.ref, host=outdated.access.host, epoch=outdated.epoch
                )
            except Exception:  # the next start closes its screen all the same
                logger.exception(
                    "outdated runner not interrupted topic=%s seat=%s",
                    seat[0],
                    seat[1],
                )
                interrupted = False
            logger.warning(
                "runner_drain_timed_out topic=%s seat=%s conversation=%s work=%s "
                "after_s=%.0f interrupted=%s",
                seat[0],
                seat[1],
                live.conversation,
                outdated.work,
                drained_for(),
                interrupted,
            )
            reason = "timed_out"
            if not interrupted:
                break
            # Asked again until it says how the work it was told to stop
            # ended, and what it read before that.
            interrupted_at = datetime.now(UTC)
        await asyncio.sleep(pkg.DRAIN_POLL_S)
    if outdated.stopped:
        reason = "stopped"
    while True:
        try:
            await _replaced(
                svc, outdated, failed=reason in ("gone", "failed", "timed_out")
            )
            break
        except Exception:  # the database away for a moment: settle again
            logger.exception(
                "runner replacement not settled topic=%s seat=%s", seat[0], seat[1]
            )
            await asyncio.sleep(pkg.DRAIN_POLL_S)
    if svc.drains.get(seat) is asyncio.current_task():
        del svc.drains[seat]
    logger.info(
        "runner_replaced topic=%s seat=%s conversation=%s reason=%s after_s=%.0f",
        seat[0],
        seat[1],
        live.conversation,
        reason,
        drained_for(),
    )


def _own(outdated: Outdated) -> list[Ending]:
    """The endings its ping gave for the work it was found doing."""
    return [e for e in outdated.endings if e.work == str(outdated.work)]


async def _replaced(svc: pkg.RoomSessions, outdated: Outdated, *, failed: bool) -> None:
    """Settle a seat whose runner is another release's, and have one of this
    release's take its place on the same conversation.

    The inputs its ping says its works read (``Outdated.endings``) are
    recorded as read, each into the work that read it, since offering them
    again would have them answered twice; whatever else it was written and
    has no record of reading is dropped, so the messages in it are offered
    again. What waited for it goes ahead. Work it finished ends as finished;
    work it did not (``failed``: it ended in a failure, was interrupted at the
    drain's cap, or its process went) ends with the upgrade as its failure,
    which the platform starts again by itself (``resumable_failures``). The
    new runner is started as soon as the seat is quiet (``prewarm_due``), or by
    the next start."""
    live = outdated.live
    seat = svc._seat_of(live.session)
    outdated.settling = True
    read = tuple((e.work, e.inputs) for e in outdated.endings if e.inputs)
    await svc._hear(live.session, Read(None, Replaced(live.conversation, read=read)))
    if (work := outdated.work) is not None:
        await svc._consume(
            live.session.project_id,
            seat[0],
            work,
            AgentResult(
                text=runner_replaced_message(live.acting) if failed else "",
                session_id=live.conversation,
                is_error=failed,
                failure_code=RUNNER_REPLACED_CODE if failed else None,
                agent_handle=live.acting,
                harness=svc.harness,
                # What this work itself read, by its own ending.
                inputs_read=any(e.inputs for e in _own(outdated)),
            ),
            f"{svc.harness}:{live.conversation}:replaced:{work}",
            False,
            False,
        )
        await svc._activity(live.session.project_id, seat, work, False)
        svc.closed.add(work)
    await svc.channel.drain_ended(live.session)
    svc.outdated.pop(seat, None)
    svc.replace_due[seat] = live
    svc._went_quiet(live.session)
