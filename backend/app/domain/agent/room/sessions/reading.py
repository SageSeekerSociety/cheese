"""Taking a seat's reading: attach it, read it, and answer for what it ends.

One reading per seat (``_poll``) hands each item the core reads to the room
(``_take``), after the room's books have taken it. ``_attach`` is what makes a
session the seat's session — under the seat's attachment lock, every swap
complete and none half-done (FB-56) — and ``_listen`` is what starts the reading
for a seat that has none. ``_died`` ends a turn nobody can answer any more, the
one ending that happens in this process rather than in the core.

``RUNNER_GONE_S`` is read here and on the starting side, and lives in the facade:
it is patched on ``app.domain.agent.room.sessions`` (``tests/support/fast_timings.py``
names that module), and the two readers that turn it into a decision are
``_died`` here and ``ensure``/``starting``. Every read goes through the package
(``pkg.RUNNER_GONE_S``) so the patch is the value this code sees.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.core.sentences import say
from app.domain.agent import attachments
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.subscription import Seat
from app.domain.agent.platform_failures import classify_session_start
from app.domain.agent.reads import (
    Abandoned,
    CaughtUp,
    Compacted,
    ControlsMoved,
    Ended,
    Followed,
    Moved,
    Reachable,
    Read,
    Received,
    Report,
    ResultLanded,
    SessionExited,
    SessionIdle,
    SessionRunning,
    Working,
    Writing,
)
from app.domain.agent.room import sessions as pkg
from app.domain.agent.room.sessions.records import Live
from app.domain.agent.service import AgentCompacting, AgentResult

logger = logging.getLogger("app.domain.agent.room.sessions")


def _seat_of(session: SessionRef) -> Seat:
    """The seat a session ref names: (conversation, agent)."""
    return (session.conversation_id, session.agent_handle)


async def _attach(svc: pkg.RoomSessions, live: Live) -> Live:
    """Make ``live`` the seat's session, read from now on by one reading
    whose events are the only ones the room's books take (FB-56)."""
    seat = svc._seat_of(live.session)
    async with attachments.lock(seat):
        # Ownership is checked FIRST, and the idempotent fast path is
        # checked INSIDE the lock with it (FB-56): an early return ahead
        # of both would wave a same-session recover through while the
        # platform is not owning sessions — the old maps still answer
        # "attached", and the caller goes on bookkeeping a recovery the
        # ownership gate never allowed.
        svc._owning(seat)
        current = svc.live.get(seat)
        if (
            current is not None
            and current == live
            and seat in svc.tasks
            and not svc.tasks[seat].done()
        ):
            return current
    await svc._detach(seat)
    attached = Live(
        live.session,
        live.ref,
        live.acting,
        live.conversation,
        attachment=uuid.uuid4().hex,
    )
    async with attachments.lock(seat):
        # The flag is checked HERE, under the lock — not once at some
        # route's entry (FB-56): while a stop is being taken, an attach
        # can land between the flag flip and the swap, and only the lock
        # makes the order real. A refused attach registers nothing.
        svc._owning(seat)
        svc.live[seat] = attached
        svc.dead = {pair for pair in svc.dead if pair[0] != seat}
        attachments.note(seat, attached.attachment)
    return attached


def _owning(svc: pkg.RoomSessions, seat: Seat) -> None:
    if svc._owns_sessions_provider is not None and (
        not svc._owns_sessions_provider().owns_sessions
    ):
        raise DeviceOffline(
            f"platform is not owning sessions right now (topic={seat[0]})"
        )


def _listen(svc: pkg.RoomSessions, seat: Seat) -> None:
    live = svc.live.get(seat)
    if live is None:
        return
    if seat not in svc.tasks or svc.tasks[seat].done():
        svc.caught[seat] = asyncio.Event()
        svc.tasks[seat] = asyncio.create_task(
            svc._poll(seat, live, svc.caught[seat]),
            name=f"{svc.label} reading {seat[0]}/{seat[1]}",
        )


def _clock_read_s(svc: pkg.RoomSessions, seat: Seat) -> float:
    """How long a reader waits for the next item before reading the seat's
    clocks: never so long that a turn's own clocks (``verdict``) are read
    late by more than a fraction of them."""
    if seat not in svc.work:
        return pkg.CLOCK_READ_S
    clocks = [s / 4 for s in (svc.no_progress_s, svc.unread_grace_s) if s > 0]
    return min([pkg.CLOCK_READ_S, *clocks])


async def _poll(
    svc: pkg.RoomSessions,
    seat: Seat,
    live: Live,
    caught: asyncio.Event,
) -> None:
    reading = svc.host.read(live.ref)
    pending: asyncio.Future | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(reading))
            done, _ = await asyncio.wait({pending}, timeout=svc._clock_read_s(seat))
            if done:
                try:
                    read = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                try:
                    if await svc._take(seat, live, read, caught):
                        return
                except Exception as exc:
                    # The core reads it again, and steps over one the room
                    # goes on refusing.
                    pending = asyncio.ensure_future(reading.athrow(exc))
                    continue
            if seat in svc.work and svc.live.get(seat) is live:
                if verdict := svc.verdict(seat):
                    await svc._end_by_verdict(live, verdict)
    finally:
        caught.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await reading.aclose()


async def _take(
    svc: pkg.RoomSessions,
    seat: Seat,
    live: Live,
    read: Read,
    caught: asyncio.Event,
) -> bool:
    """Keep the room's books on one item its session's reading handed over,
    and hand it on to the room. True when the reading is over."""
    project, topic = live.session.project_id, seat[0]
    event = read.event
    work = uuid.UUID(read.work_id) if read.work_id else None
    if isinstance(event, Working):
        assert work is not None
        await svc._activity(project, seat, work, event.active)
        if not event.active:
            # 这一段工作结束时问一次记忆（不报会话状态的骨架；Claude Code 在
            # runner 报会话空闲时问，见 `_keep`）：agent 该写的记忆按规矩写在
            # 回复之前，所以工作停下来就是它写完的时刻。
            await svc.reconcile_memory(live.session)
            svc._went_quiet(live.session)
    elif isinstance(event, Moved):
        svc.pulse(seat, event.marks)
    elif isinstance(event, Received):
        await svc._hear_receipt(event.receipt)
    elif isinstance(event, Report):
        return await _report(svc, seat, live, event)
    elif isinstance(event, Writing):
        # What the agent is in the middle of writing, under its own name.
        work = work or svc.work.get(seat)
        await svc._hear(
            live.session,
            Read(
                str(work) if work else None,
                Writing(event.blocks, author=live.acting),
            ),
        )
    elif isinstance(event, Reachable):
        if event.yes:
            await svc._say_resumed(seat)
        else:
            await svc._say_waiting(seat, event.reason)
    elif isinstance(event, Ended):
        if seat in svc.work and svc.live.get(seat) is live:
            await svc._died(
                live, out_of_reach=event.reason == "out of reach", said=event.said
            )
        return True
    elif isinstance(event, CaughtUp):
        caught.set()
    elif isinstance(event, ControlsMoved):
        await svc.announce(topic)
        # A background task finishing is what lets a relaunch put off for
        # it happen; the controls moving is how that is heard.
        svc._went_quiet(live.session)
    else:
        assert work is not None, "a session's events belong to work"
        if hasattr(event, "attachment"):
            # Which reading this came from (FB-56): a replaced one's is
            # refused where the room's books change.
            event.attachment = live.attachment  # type: ignore[union-attr]
        await svc._consume(
            project,
            topic,
            work,
            event,
            read.eid,
            read.text_seen,
            read.unsolicited,
        )
        if isinstance(event, AgentCompacting) and event.done and not event.error:
            # Whichever way it came — the harness's own when the window
            # filled, or `cheese_session(action=compact)` — the event names no seat, and what
            # the room forgets is this seat's.
            await svc._hear(live.session, Read(str(work), Compacted()))
    return False


async def _report(
    svc: pkg.RoomSessions, seat: Seat, live: Live, report: Report
) -> bool:
    """What a seat's runner reported: applied to the database first, once
    (``agent.runner_reports``), then kept in the room's books, and then marked
    followed through. A failure on the way leaves it unmarked and, the reading
    not acknowledging it, delivered again: the database half is not applied a
    second time and the rest is done again. Never ends the reading itself: a
    session that is gone ends it once its runner has heard the last event was
    taken."""
    applied = await svc._hear(live.session, Read(None, report), required=True)
    if applied is False:
        # Applied and followed through before (a redelivery): everything that
        # follows from it was done then.
        return False
    await _keep(
        svc, seat, live, report, landed=bool(getattr(applied, "result_landed", False))
    )
    await svc._hear(live.session, Read(None, Followed(report)), required=True)
    return False


async def _keep(
    svc: pkg.RoomSessions, seat: Seat, live: Live, report: Report, *, landed: bool
) -> None:
    """The room's books on what a seat's runner reported. Each part is
    idempotent: an event delivered again after a failure here is kept again,
    and the closing result it carries, already handed to the room in an earlier
    delivery (``landed``), is not handed again."""
    project, fact = live.session.project_id, report.fact
    if isinstance(fact, SessionRunning) and fact.work_id is not None:
        await svc._activity(project, seat, fact.work_id, True)
    elif isinstance(fact, SessionIdle) and fact.work_id is not None:
        result = fact.result
        if not landed:
            await svc._consume(
                project,
                seat[0],
                fact.work_id,
                result,
                f"{svc.harness}:{report.conversation_id}:{report.agent_handle}"
                f":{report.epoch}:{report.seq}",
                # The closing text was already landed as its own message; the
                # result must not publish it twice.
                not result.is_error,
                fact.unsolicited,
            )
            await svc._hear(
                live.session, Read(None, ResultLanded(report)), required=True
            )
        await svc._activity(project, seat, fact.work_id, False)
        # 会话空闲时问一次记忆：agent 该写的记忆按规矩写在回复之前，所以它停下
        # 来就是它写完的时刻。
        await svc.reconcile_memory(live.session)
        svc._went_quiet(live.session)
    elif isinstance(fact, SessionExited) and report.of_this_registration:
        if svc.live.get(seat) is live:
            svc.live.pop(seat, None)
            # Its runner said so: the conversation is over on this seat, which
            # is death, not an unknown (FB-56).
            svc.dead.add((seat, live.conversation))
        # The reading ends on its own once it has acknowledged this.


async def _say_waiting(svc: pkg.RoomSessions, seat: Seat, reason: str) -> None:
    """Tell the room this seat's open turn is waiting on the machine — once
    per turn per outage. A seat with no turn open is not waiting for
    anything: its machine being off is the ordinary state of a platform
    nobody is using."""
    work = svc.work.get(seat)
    live = svc.live.get(seat)
    if work is None or live is None or svc.told_waiting.get(seat) == work:
        return
    svc.told_waiting[seat] = work
    await svc._hear(live.session, Read(str(work), Reachable(False, reason)))


async def _say_resumed(svc: pkg.RoomSessions, seat: Seat) -> None:
    work = svc.told_waiting.pop(seat, None)
    live = svc.live.get(seat)
    if work is None or live is None or svc.work.get(seat) != work:
        return
    await svc._hear(live.session, Read(str(work), Reachable(True)))


async def _died(
    svc: pkg.RoomSessions,
    live: Live,
    *,
    out_of_reach: bool = False,
    said: str = "",
) -> None:
    """The turn is over with nobody able to answer it. Say so where the turn
    is, or the room waits on something that will never answer.

    Two things end a turn this way and the room is told which: a runner that
    answered and said its process was gone, and one that has not answered for
    ``RUNNER_GONE_S`` at all. The second is not the same fact as the first —
    a runner that never came up is the shape of a machine still being
    prepared, and saying its process exited then is a sentence the room reads
    as an unexplained crash. Whoever reads either one has the socket, the
    machine and the runner's log to go on; the platform does not, so it says
    only what it saw. A process its runner recorded ending before it ever
    started (``said``) is a session that did not start, and the room hears
    why, as it would had the start itself failed.
    """
    seat = svc._seat_of(live.session)
    work = svc.work[seat]
    refused = classify_session_start(said, harness=svc.label) if said else None
    await svc._consume(
        live.session.project_id,
        seat[0],
        work,
        AgentResult(
            text=(
                refused.content
                if refused is not None
                else say(
                    "runnerUnresponsive",
                    harness=svc.label,
                    seconds=f"{pkg.RUNNER_GONE_S:.0f}",
                )
                if out_of_reach
                else f"{svc.label} session process exited"
            ),
            session_id=live.conversation,
            is_error=True,
            failure_code=refused.code if refused is not None else None,
            log=said or None,
            agent_handle=live.acting,
            harness=svc.harness,
        ),
        f"{svc.harness}:{live.conversation}:exit:{work}",
        False,
        False,
    )
    await svc._activity(live.session.project_id, seat, work, False)
    svc.closed.add(work)
    svc.live.pop(seat, None)
    if not out_of_reach:
        # A verdict/exit the runner itself reported is death. Merely
        # being out of reach is not — a machine still being prepared
        # looks exactly the same, and that one stays "unknown".
        svc.dead.add((seat, live.conversation))
        # Nothing will report on the inputs this work held: the runner that
        # would have reported them read or dropped is the process that is
        # gone. The turn's interval ends with the result above, which is
        # what puts it out of the orphan sweep's reach, so the room ends
        # them here or they hold a seat no later message can take.
        await svc._hear(live.session, Read(str(work), Abandoned()))
