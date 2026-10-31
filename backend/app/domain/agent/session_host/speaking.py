"""Saying something to a session that is running.

``send`` is an input the session answers as work of its own; ``steer`` is a
word in the middle of the work it is already doing. Both go through ``_say``,
which is the one call, and a runner that is not there to take it (``_NotThere``)
is either a session let go while idle — started again on its conversation and
told once more — or an input that may have been taken (``InputUnconfirmed``).

``_spoken_to`` is whether anything was said to the session yet, which decides
whether a prompt's preface is still owed to it; ``answers`` is whether its
runner answered its last read. ``SessionHost`` forwards to these.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.pi.events import thread_of
from app.domain.agent.session_host.contract import (
    InputUnconfirmed,
    Prompt,
    SessionRef,
)
from app.domain.agent.session_host.sessions import _Running

if TYPE_CHECKING:
    from app.domain.agent.session_host import SessionHost


class _NotThere(Exception):
    """A call no runner took: the host or the runner was not there."""


async def send(
    svc: SessionHost, ref: SessionRef, prompt: Prompt, *, work_id: uuid.UUID
) -> None:
    """Say ``prompt`` to the session, as work ``work_id``: what it does
    about it is read (``read``). Raises ``InputUnconfirmed`` when the
    runner may have taken it and did not say so."""
    running = svc._started(ref)
    try:
        await _say(
            svc,
            ref,
            running,
            "send",
            prompt,
            await _text(svc, ref, running, prompt),
            work_id,
        )
    except _NotThere:
        if running.spec is None or running.access is None:
            raise InputUnconfirmed(accepted=False) from None
        # The runner let the session go since it was last read (it sat
        # idle), before it was asked what it had heard or told this: started
        # again on its conversation, it is told once more. The runner keeps
        # the inputs it took by id, so one that did reach it is not taken
        # twice.
        running.answering = False
        await svc.start(ref, running.spec, running.access)
        running = svc._started(ref)
        try:
            text = await _text(svc, ref, running, prompt)
            await _say(svc, ref, running, "send", prompt, text, work_id)
        except _NotThere as exc:
            raise InputUnconfirmed(accepted=False) from exc.__cause__
    running.spoken_to = True


async def _text(
    svc: SessionHost, ref: SessionRef, running: _Running, prompt: Prompt
) -> str:
    """What is said: with the preface, to a session nothing was said to yet."""
    if not prompt.preface:
        return prompt.text
    try:
        spoken_to = await _spoken_to(svc, ref, running)
    except (DeviceOffline, DeviceCallError) as exc:
        running.spoken_to = None
        raise _NotThere() from exc
    return prompt.text if spoken_to else f"{prompt.preface}\n\n{prompt.text}"


async def steer(
    svc: SessionHost, ref: SessionRef, prompt: Prompt, *, work_id: uuid.UUID
) -> None:
    """Say ``prompt`` to the session in the middle of work ``work_id``,
    without opening work of its own. Raises ``InputUnconfirmed`` as
    ``send`` does."""
    running = svc._started(ref)
    method = svc._driver(ref).steer
    try:
        await _say(svc, ref, running, method, prompt, prompt.text, work_id)
    except _NotThere as exc:
        raise InputUnconfirmed(accepted=False) from exc.__cause__


async def act_as(svc: SessionHost, ref: SessionRef, acting: str) -> None:
    """Have what the session's tools act with be ``acting`` from now on,
    saying nothing to it: a long answer's credential renewed before it
    runs out."""
    running = svc._started(ref)
    await svc._call(running, ref, "acting", {"platform_token": acting})


async def _say(
    svc: SessionHost,
    ref: SessionRef,
    running: _Running,
    method: str,
    prompt: Prompt,
    text: str,
    work_id: uuid.UUID,
) -> None:
    params: dict[str, Any] = {
        "input_id": str(prompt.id),
        "work_id": str(work_id),
        "text": text,
    }
    if prompt.images:
        params["images"] = svc._driver(ref).images(prompt.images)
    if prompt.owes_reply:
        params["owes_reply"] = True
    if prompt.acting is not None:
        params["platform_token"] = prompt.acting
    running.owed = True
    if running.launched.epoch:
        # Recorded before it leaves: an exit of the process it was handed to
        # before must not be read as this one's (`delivery.receipts.hand_to`).
        await svc._handing(ref, running, prompt.id)
    try:
        await svc._call(running, ref, method, params)
    except (DeviceOffline, DeviceCallError) as exc:
        # Nothing on the host took it: the host is not connected, or the
        # runner is not there to answer.
        raise _NotThere() from exc
    except Exception as exc:
        # Even a transport error can follow admission at the runner.
        raise InputUnconfirmed(accepted=False) from exc
    finally:
        svc._wake(running)


def answers(svc: SessionHost, ref: SessionRef) -> bool | None:
    """Whether the session's runner answered its last read; False once it
    stopped — most often because it let its idle session go — and None
    when this process holds no such session."""
    running = svc._running.get(ref)
    return None if running is None else running.answering


async def _spoken_to(svc: SessionHost, ref: SessionRef, running: _Running) -> bool:
    """Whether anything was said to the session yet: a session started
    for a conversation that has history somewhere else has not been."""
    if running.spoken_to is None:
        running.spoken_to = False
        since = None
        while True:
            page = (
                await svc._wire().call(running.host, ref, "entries", {"since": since})
            )["entries"]
            for record in page:
                since = record.get("id") or since
                message = record.get("message") or {}
                if not thread_of(record) and message.get("role") == "user":
                    running.spoken_to = True
            if len(page) < PAGE:
                break
    return running.spoken_to
