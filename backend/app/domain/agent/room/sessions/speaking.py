"""Putting words into a seat's session: a message or a steer.

``send`` opens the work (a message that owes an answer), ``steer`` adds words to a
turn already running, and both end at ``_submit``, which is the one call that
hands a prompt to the host and is the one place a lost acknowledgement is told
apart from an input that never went in.

``send`` logs one ``room_send_timing`` line per message, under this module's
logger — the name is ``app.domain.agent.room.sessions``, the path the module was
reached by before the package split, so ``caplog`` filtering on it still sees
those lines.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.subscription import Seat
from app.domain.agent.room.sessions.records import Live
from app.domain.agent.room.stopwatch import timed
from app.domain.agent.service import AgentSessionInfo
from app.domain.agent.session_host.contract import (
    Image,
    InputUnconfirmed,
    Prompt,
    SessionError,
)
from app.domain.delivery.input_identity import (
    InputIdentity,
    InputNotSent,
    InputOutcomeUnconfirmed,
    InputReceipt,
    InputRegistrar,
)

if TYPE_CHECKING:
    from app.domain.agent.room import sessions as pkg

logger = logging.getLogger("app.domain.agent.room.sessions")


async def send(
    svc: pkg.RoomSessions,
    session: SessionRef,
    message: str,
    *,
    system_prompt: str,
    work_id: uuid.UUID,
    on_mark: Callable[[uuid.UUID], None],
    register_input: InputRegistrar,
    resume_token: str | None = None,
    model: str | None = None,
    env: dict[str, str] | None = None,
    acting: str | None = None,
    needs_place: bool = True,
    keeps_nothing: bool = False,
    images: list[Image] | None = None,
    owes_reply: bool = False,
    session_opening: str = "",
    opening_changes: str = "",
) -> bool:
    """Put a message into the seat's session, as work ``work_id``, starting
    the session if it has to be. True = the session's runner took it.

    ``session_opening`` is the project state a new conversation is handed in
    front of its first message; ``opening_changes`` the part of it that
    changed since a conversation that goes on last heard (`_with_project_state`).

    An ack, not an answer: what the agent does about it arrives through the
    seat's reading — possibly minutes later, possibly to a different process
    than the one that sent this. ``owes_reply``: a person wrote this, and
    the session answers them in the room before it does anything else
    (`driven/runner.py`)."""
    # One line per message put to a session: where the platform spent the
    # time between the turn opening and the runner holding the input.
    with timed(
        logger,
        "room_send_timing",
        topic=session.conversation_id,
        seat=session.agent_handle,
        work=work_id,
    ) as mark:
        seat = svc._seat_of(session)
        live = await svc.ensure(
            session,
            system_prompt=system_prompt,
            resume_token=resume_token,
            model=model,
            env=env,
            acting=acting,
            needs_place=needs_place,
            keeps_nothing=keeps_nothing,
            phases=mark.phases,
        )
        mark.lap()
        message = svc._with_project_state(
            seat, live, resume_token, session_opening, opening_changes, message
        )
        pictures = tuple(images or ())
        on_mark(work_id)
        await svc._consume(
            session.project_id,
            session.conversation_id,
            work_id,
            AgentSessionInfo(
                session_id=live.conversation,
                agent_handle=live.acting,
                harness=svc.harness,
            ),
            f"{svc.harness}:{live.conversation}:opening:{work_id}",
            False,
            False,
        )
        mark("consume")
        # A message sent while the session is in the middle of a turn is read at
        # that turn's next tool boundary and answered inside it: its records,
        # its result and its end all carry the running turn's work, and none
        # ever names this one. Taking the seat over would leave it holding a
        # turn that never ends, and the next time the session goes away (the
        # runner lets an idle one go) that turn would be failed as a crash.
        if seat not in svc.clocks:
            svc.work[seat] = work_id
        # 记忆先落到会话目录里，输入后写进去：agent 这一轮一睁眼读到的应当是平台
        # 现在这一份（别人刚改的也在里面），而不是它上一次看见的那一份。
        await svc.reconcile_memory(session)
        mark("memory")
        identity = InputIdentity(
            session.project_id,
            session.conversation_id,
            live.acting,
            svc.harness,
            live.conversation,
            work_id,
            work_id,
        )
        await register_input(identity)
        mark("register")
        try:
            await svc._submit(
                live,
                identity,
                Prompt(work_id, message, images=pictures, owes_reply=owes_reply),
                steer=False,
                register_input=register_input,
            )
            mark("submit")
        finally:
            # A lost acknowledgement does not mean the session stopped working.
            svc._listen(seat)
        return True


def _with_project_state(
    svc: pkg.RoomSessions,
    seat: Seat,
    live: Live,
    resume_token: str | None,
    session_opening: str,
    opening_changes: str,
    message: str,
) -> str:
    """The message with the project state this conversation has not heard.

    A conversation is new when it is not the one the caller asked to resume:
    no token, or a resume that failed and started afresh. A new one gets the
    whole opening; one that goes on gets what changed since it last heard.
    A second message before the new conversation's token is recorded must
    not hand it the opening again, so each seat remembers the conversation
    it opened. After a backend restart that is gone, and the most a lost
    entry costs is an opening handed twice."""
    conversation = live.conversation
    fresh = conversation != resume_token and svc.opened.get(seat) != conversation
    svc.opened[seat] = conversation
    state = session_opening if fresh else opening_changes
    return f"{state}\n\n{message}" if state else message


async def steer(
    svc: pkg.RoomSessions,
    topic_id: uuid.UUID,
    text: str,
    images: list[Image] | None = None,
    *,
    register_input: InputRegistrar,
    agent_handle: str | None = None,
    owes_reply: bool = False,
) -> bool:
    """Words for a session that is already working, with no work opened for
    them: a person's message mid-work, or the platform telling a working
    session the world changed under it. True = it landed; False = no seat
    here is working.

    Addressed by room rather than by session because the caller is on the
    hot path with a person waiting and has no project id in hand.
    ``agent_handle`` names the seat when the caller knows whose session this
    is for; without it the room must have exactly one working seat — a guess
    between two working teammates would put the words into the wrong
    conversation. Whether the session is working is its runner's answer
    (``status``), asked right before the words go in.
    """
    seat = svc._steer_seat(topic_id, agent_handle)
    if seat is None:
        return False
    live = svc.live.get(seat)
    work = svc.work.get(seat)
    if live is None or work is None:
        return False
    status = await svc.host.status(live.ref)
    if status is None or not status.working:
        return False
    if svc.live.get(seat) is not live or svc.work.get(seat) != work:
        return False
    identity = InputIdentity(
        live.session.project_id,
        topic_id,
        live.acting,
        svc.harness,
        live.conversation,
        uuid.uuid4(),
        work,
    )
    pictures = tuple(images or ())
    await register_input(identity)
    await svc._submit(
        live,
        identity,
        Prompt(identity.input_id, text, images=pictures, owes_reply=owes_reply),
        steer=True,
        register_input=register_input,
    )
    return True


async def _submit(
    svc: pkg.RoomSessions,
    live: Live,
    identity: InputIdentity,
    prompt: Prompt,
    *,
    steer: bool,
    register_input: InputRegistrar,
) -> None:
    accepted = False
    try:
        say_it = svc.host.steer if steer else svc.host.send
        await say_it(live.ref, prompt, work_id=identity.work_id)
        accepted = True
        # Only a harness whose acceptance *is* the read reports here. A
        # harness that says when its session read the input (Claude Code)
        # leaves this to the echo, so a consumer still holding the receipt
        # must never gate the send that admits it.
        if svc.host.reads_on_accept(live.ref):
            await svc._hear_receipt(InputReceipt(identity, "accepted"))
    except SessionError as exc:
        if accepted or isinstance(exc, InputUnconfirmed):
            raise InputOutcomeUnconfirmed(identity, accepted=accepted) from exc
        # The host raises InputUnconfirmed for anything after the write was
        # attempted; any other SessionError means it never got that far (the
        # session is not this process's, or could not be started again), so
        # the input is certainly not in the session: nothing to reconcile.
        await register_input.withdraw(identity)
        raise InputNotSent(identity, str(exc)) from exc
    except Exception as exc:
        # Even a transport error can follow admission at the remote end.
        # Keep the committed identity; the caller must not queue a new UUID.
        raise InputOutcomeUnconfirmed(identity, accepted=accepted) from exc


def _steer_seat(svc: pkg.RoomSessions, topic_id, agent_handle) -> Seat | None:
    """Which seat words addressed to a room mean, or None when ambiguous."""
    if agent_handle is not None:
        return (topic_id, agent_handle)
    working = [seat for seat in svc.work if seat[0] == topic_id]
    return working[0] if len(working) == 1 else None
