"""A person brings a task's machine back for its files, with no turn.

A task's working copy is in the environment the task points to. When
that sandbox is gone — destroyed or parked by the idle sweep, or its executor
stopped for sitting idle — a person editing the task's files should not have
to wait for the AI teammate to start a turn. Opening the files reads them off
the forge (`repository.forge_files`); editing them brings the machine back:

- in the environment the task points to, the one machine and the one copy
  its teammates work in (no second environment, no second copy), acting
  through a session attached to it (`acting_session`);
- through the same admission a tool call goes through
  (`session_work.hands_for_a_person`): a parked sandbox starts again where its
  home is, a destroyed one is placed anew and its executor installed, a frozen
  one is thawed, an executor that stopped is started; two callers at once —
  a person and a turn — are ordered by the environment's claim (`lease_claim`), and
  the one that waits gets the machine the other brought up;
- in the person's name, whose project pays for it as it does for the teammate
  (`holder.Room.require_use`), on the sandbox's usual meter (`metering`);
- then the task's checkout is opened there (`cheese worktree`, which lays back
  what the last sandbox kept) as the platform's own call on the executor
  (`task_checkout.checkout_request`), which is no tool call of the teammate's
  and nothing said in the conversation.

What the teammate is owed about its sandbox (`Attachment.notice`) is left
for its own next tool call.

The bring-up runs in the background, one per task in this process, so a person
who asked for it is answered at once and asks how far it got
(`status`), as an app preview's wake is (`preview_wake`). A save that arrives
meanwhile waits for it a short while (`prepare_for_write`).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, UpstreamUnavailableError
from app.core.sentences import say
from app.domain.agent import execution
from app.domain.agent.task_checkout import (
    CHECKOUT_TIMEOUT_MS,
    checkout_answered,
    checkout_request,
)
from app.domain.agent_session.models import AgentSession
from app.domain.environment import services as environment_rows
from app.domain.identity.actor import Actor
from app.domain.machine import holder as holders
from app.domain.machine import session_work
from app.domain.machine.lease_claim import still_preparing

logger = logging.getLogger(__name__)

#: How long a bring-up keeps asking for a machine still being prepared: a new
#: Cloud host can take minutes to come up.
BRING_UP_S = 15 * 60
#: How long one ask waits for a machine being prepared before asking again.
ROUND_S = 60.0
#: How long a save waits for the machine it brought back before it is answered
#: as one still being prepared.
SAVE_WAIT_S = 20.0

_running: dict[uuid.UUID, asyncio.Task[None]] = {}
#: Why the last bring-up asked for here did not finish: ``no_session`` (the
#: task has no session to bring a machine back for) or ``unavailable`` (no
#: machine could be had for it; what the machine said is in the log).
_failed: dict[uuid.UUID, str] = {}
NO_SESSION = "no_session"
UNAVAILABLE = "unavailable"


class NoTaskSession(ConflictError):
    """The task has no agent session to bring a machine back for: nobody has
    worked in it on a machine, so there is no copy and no lease to take."""

    def __init__(self) -> None:
        super().__init__(say("taskMachineNoSession"))


class MachinePreparing(UpstreamUnavailableError):
    """The task's machine is still being brought back. The same request can
    be sent again once it is ready (`status`)."""

    retryable = True

    def __init__(self) -> None:
        super().__init__(say("taskMachinePreparing"))


@dataclass(frozen=True)
class Status:
    #: ``ready``: a machine holds the task's copy; ``preparing``: one is being
    #: brought back (here, or a turn of its teammate's is installing it);
    #: None: none holds it.
    machine: str | None
    #: Why the last bring-up asked for here did not finish (``NO_SESSION``,
    #: ``UNAVAILABLE``); None when it did, or none was asked.
    failed: str | None


async def acting_session(db: AsyncSession, task_id: uuid.UUID) -> AgentSession | None:
    """The session a person's bring-up of the task's machine acts through:
    one attached to the environment the task points to
    (`environment_rows.environment_of_conversation`), the one that acted
    there last. A bring-up is admitted, placed and credentialed as a
    session's tool call is (`session_work.hands_for_a_person`), and a person
    is no attachment yet, so it needs one; the environment itself is found
    from the task alone. None while the task points to no environment, or
    nobody is attached to the one it points to."""
    environment = await environment_rows.environment_of_conversation(db, task_id)
    if environment is None:
        return None
    for attached in await environment_rows.attachments_of(db, environment.id):
        row = await db.get(AgentSession, attached.session_id)
        if row is not None and row.conversation_id == task_id:
            return row
    return None


async def prepare(
    db: AsyncSession, task_id: uuid.UUID, actor: Actor, *, hub=None
) -> bool:
    """Start bringing the task's machine back for ``actor``, unless that is
    already under way here. Answers whether this call started it. Refuses a
    task with no session, and a person the task's project would not start a
    sandbox for, before anything is started."""
    row = await acting_session(db, task_id)
    if row is None:
        raise NoTaskSession()
    holder = await holders.look(db, await holders.holder_of(db, task_id))
    if holder is None:
        raise NoTaskSession()
    await holder.require_use(db, actor)
    await db.commit()
    if task_id in _running:
        return False
    _failed.pop(task_id, None)
    work = asyncio.create_task(_bring_up(db.bind, task_id, actor, hub=hub))
    _running[task_id] = work
    work.add_done_callback(lambda _done: _running.pop(task_id, None))
    return True


async def prepare_for_write(
    bind, actor: Actor, task_id: uuid.UUID, *, hub=None, wait_s: float | None = None
) -> None:
    """A person's save found no machine holding the task's copy: bring one
    back (`prepare`) and wait up to ``wait_s`` for it
    (`repository.ports.PrepareWorkingCopy`). Raises ``MachinePreparing`` when
    it is not ready by then, and the bring-up's own refusal when it failed."""
    async with AsyncSession(bind, expire_on_commit=False) as db:
        await prepare(db, task_id, actor, hub=hub)
    work = _running.get(task_id)
    if work is not None:
        try:
            await asyncio.wait_for(
                asyncio.shield(work), SAVE_WAIT_S if wait_s is None else wait_s
            )
        except TimeoutError:
            raise MachinePreparing() from None
    failed = _failed.get(task_id)
    if failed == NO_SESSION:
        raise NoTaskSession()
    if failed is not None:
        raise UpstreamUnavailableError(say("taskMachinePrepareFailed"))


async def status(db: AsyncSession, task_id: uuid.UUID) -> Status:
    """How far bringing the task's machine back has got."""
    failed = _failed.get(task_id)
    if task_id in _running:
        return Status(machine="preparing", failed=None)
    environment = await environment_rows.environment_of_conversation(db, task_id)
    lease = (
        await environment_rows.view(db, environment.id) if environment else None
    ) or {}
    if still_preparing(lease):
        return Status(machine="preparing", failed=failed)
    if lease.get("kind") == "device" and lease.get("status", "ready") == "ready":
        return Status(machine="ready", failed=None)
    return Status(machine=None, failed=failed)


async def _bring_up(bind, task_id: uuid.UUID, actor: Actor, *, hub=None) -> None:
    """Take the task's session's hands for ``actor`` until they are ready or
    refused, then open the task's checkout on them. A failure is kept for
    `status`, in a sentence for the person; what the machine said is logged."""
    try:
        deadline = time.monotonic() + BRING_UP_S
        async with AsyncSession(bind, expire_on_commit=False) as db:
            while True:
                row = await acting_session(db, task_id)
                if row is None:
                    raise NoTaskSession()
                outcome = await session_work.hands_for_a_person(
                    db, row, actor=actor, wait_s=ROUND_S, hub=hub
                )
                await db.commit()
                if "target" in outcome:
                    break
                if not outcome.get("preparing") or time.monotonic() >= deadline:
                    logger.warning(
                        "bringing task %s's machine back: %s",
                        task_id,
                        outcome.get("unavailable"),
                    )
                    _failed[task_id] = UNAVAILABLE
                    return
        answer = await execution.call(
            outcome["target"],
            "invoke",
            {
                **checkout_request(task_id, prefix="person-checkout-"),
                # Whose the command is: the person, in the task. A person is
                # no session attached to the machine, so it names no
                # attachment; what it asks the platform back is answered on
                # the environment's own authority (`machine.caller`).
                "caller": {"conversation": str(task_id), "author": actor.handle},
            },
            timeout=CHECKOUT_TIMEOUT_MS / 1000 + 30,
        )
        checkout_answered(answer)
    except NoTaskSession:
        _failed[task_id] = NO_SESSION
    except Exception:
        logger.warning("bringing task %s's machine back failed", task_id, exc_info=True)
        _failed[task_id] = UNAVAILABLE
