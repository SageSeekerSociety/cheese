"""A person brings a task's machine back for its files, with no turn.

A task's working copy is in the environment the task points to. When
that sandbox is gone — destroyed or parked by the idle sweep, or its executor
stopped for sitting idle — a person editing the task's files should not have
to wait for the AI teammate to start a turn. Opening the files reads them off
the forge (`repository.forge_files`); editing them brings the machine back:

- in the environment where the task's work is, the one machine and the one
  copy its teammates work in (no second environment, no second copy), or
  the task's default one when nobody has worked in it yet; the person is
  attached to it with an identity of their own;
- through the same admission a tool call goes through
  (`session_work.ensure`): a parked sandbox starts again where its
  home is, a destroyed one is placed anew and its executor installed, a frozen
  one is thawed, an executor that stopped is started; two callers at once —
  a person and a turn — are ordered by the environment's claim (`lease_claim`), and
  the one that waits gets the machine the other brought up;
- in the person's name, whose project pays for it as it does for the teammate
  (`holder.Room.require_use`), on the sandbox's usual meter (`metering`);
- then the task's checkout is opened there (`cheese worktree`, which lays back
  what the last sandbox kept) as the person's command on the executor
  (`task_checkout.checkout_request`), answered on the environment's authority
  (`machine.caller`), which is no tool call of the teammate's and nothing
  said in the conversation.

What the teammate is owed about its sandbox (`Attachment.notice`) is left
for its own next tool call.

The bring-up runs in the background, one per environment whichever backend
process runs it (``Environment.preparing_until``), so a person who asked for
it is answered at once and asks how far it got (`status`), as an app
preview's wake is (`preview_wake`). A save that arrives meanwhile waits for
it a short while (`prepare_for_write`).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UpstreamUnavailableError
from app.core.sentences import say
from app.domain.agent import execution
from app.domain.agent.task_checkout import (
    CHECKOUT_TIMEOUT_MS,
    checkout_answered,
    checkout_request,
)
from app.domain.environment import services as environment_rows
from app.domain.environment.models import Environment
from app.domain.identity.actor import Actor
from app.domain.machine import holder as holders
from app.domain.machine import session_work
from app.domain.machine.lease_claim import still_preparing
from app.domain.user.services import user_by_handle

logger = logging.getLogger(__name__)

#: How long a bring-up keeps asking for a machine still being prepared: a new
#: Cloud host can take minutes to come up. A bring-up somebody asked for
#: stands this long (``Environment.preparing_until``) unless it ends first.
BRING_UP_S = 15 * 60
#: How long one ask waits for a machine being prepared before asking again.
ROUND_S = 60.0
#: How long a save waits for the machine it brought back before it is answered
#: as one still being prepared.
SAVE_WAIT_S = 20.0
#: How often a save waiting for the machine looks at how far it got.
SAVE_POLL_S = 0.25
#: Why the last bring-up somebody asked for did not finish: no machine could
#: be had for it (what the machine said is in the log).
UNAVAILABLE = "unavailable"

#: The bring-ups this process runs, held so the event loop keeps them.
_BRING_UPS: set[asyncio.Task[None]] = set()


class MachinePreparing(UpstreamUnavailableError):
    """The task's machine is still being brought back. The same request can
    be sent again once it is ready (`status`)."""

    retryable = True

    def __init__(self) -> None:
        super().__init__(say("taskMachinePreparing"))


@dataclass(frozen=True)
class Status:
    #: ``ready``: a machine holds the task's copy; ``preparing``: one is being
    #: brought back (asked for by somebody, or a turn of its teammate's is
    #: installing it); None: none holds it.
    machine: str | None
    #: Why the last bring-up somebody asked for did not finish
    #: (``UNAVAILABLE``); None when it did, or none was asked.
    failed: str | None


async def prepare(
    db: AsyncSession, task_id: uuid.UUID, actor: Actor, *, hub=None
) -> bool:
    """Attach ``actor`` to where the task's work is and start bringing its
    machine back, unless a bring-up of that environment is already under
    way — in this process or any other. Answers whether this call started
    it. Refuses a person the task's project would not start a sandbox for
    before anything is started."""
    holder = await holders.look(db, await holders.holder_of(db, task_id))
    if holder is None:
        raise NotFoundError(say("taskNotFound"))
    await holder.require_use(db, actor)
    person = actor
    if person.user_id is None:
        user = await user_by_handle(db, actor.handle)
        if user is None:
            raise NotFoundError(say("taskNotFound"))
        person = replace(actor, user_id=user.id)
    attacher = session_work.Attacher.person(
        person, task_id, await session_work.person_launch_values(db, task_id)
    )
    environment_id = await session_work.attach_person(db, attacher)
    now = datetime.now(UTC)
    # One bring-up per environment: whoever moves this first runs it.
    started = await db.scalar(
        update(Environment)
        .where(
            Environment.id == environment_id,
            or_(
                Environment.preparing_until.is_(None),
                Environment.preparing_until <= now,
            ),
        )
        .values(preparing_until=now + timedelta(seconds=BRING_UP_S), failure=None)
        .returning(Environment.id)
    )
    await db.commit()
    if started is None:
        return False
    work = asyncio.create_task(
        _bring_up(db.bind, task_id, attacher, environment_id, hub=hub)
    )
    _BRING_UPS.add(work)
    work.add_done_callback(_BRING_UPS.discard)
    return True


async def prepare_for_write(
    bind, actor: Actor, task_id: uuid.UUID, *, hub=None, wait_s: float | None = None
) -> None:
    """A person's save found no machine holding the task's copy: bring one
    back (`prepare`) and wait up to ``wait_s`` for it
    (`repository.ports.PrepareWorkingCopy`). Raises ``MachinePreparing`` when
    it is not ready by then, and a refusal when the bring-up failed."""
    async with AsyncSession(bind, expire_on_commit=False) as db:
        await prepare(db, task_id, actor, hub=hub)
    deadline = time.monotonic() + (SAVE_WAIT_S if wait_s is None else wait_s)
    while True:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            now = await status(db, task_id)
        if now.machine == "ready":
            return
        if now.machine is None:
            if now.failed is not None:
                raise UpstreamUnavailableError(say("taskMachinePrepareFailed"))
            return
        if time.monotonic() >= deadline:
            raise MachinePreparing()
        await asyncio.sleep(SAVE_POLL_S)


async def status(db: AsyncSession, task_id: uuid.UUID) -> Status:
    """How far bringing the task's machine back has got: a fact about the
    environment where the task's work is, the same from every process."""
    environment = await environment_rows.environment_of_conversation(db, task_id)
    if environment is None:
        return Status(machine=None, failed=None)
    lease = (await environment_rows.view(db, environment.id)) or {}
    if (
        environment.preparing_until is not None
        and environment.preparing_until > datetime.now(UTC)
    ) or still_preparing(lease):
        return Status(machine="preparing", failed=None)
    if lease.get("kind") == "device" and lease.get("status", "ready") == "ready":
        return Status(machine="ready", failed=None)
    return Status(machine=None, failed=environment.failure)


async def _never_gone() -> bool:
    return False


async def _bring_up(
    bind,
    task_id: uuid.UUID,
    attacher: session_work.Attacher,
    environment_id: uuid.UUID,
    *,
    hub=None,
) -> None:
    """Take hands for the person where the task's work is until they are
    ready or refused (`session_work.ensure`), then open the task's checkout
    on them. Whatever the end, the bring-up is over; a failure is kept on the
    environment for `status`, and what the machine said is logged."""
    failure = None
    try:
        deadline = time.monotonic() + BRING_UP_S
        async with AsyncSession(bind, expire_on_commit=False) as db:
            holder_id = await holders.holder_of(db, task_id)
            await db.commit()
            while True:
                outcome = await session_work.ensure(
                    db,
                    topic_id=holder_id,
                    attacher=attacher,
                    wait_s=ROUND_S,
                    gone=_never_gone,
                    hub=hub,
                    whole_check=True,
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
                    failure = UNAVAILABLE
                    return
        assert attacher.actor is not None  # a person
        answer = await execution.call(
            outcome["target"],
            "invoke",
            {
                **checkout_request(task_id, prefix="person-checkout-"),
                # Whose the command is: the person's attachment, in the task.
                # What it asks the platform back is answered on the
                # environment's authority in the person's name
                # (`machine.caller`), never on the person's own.
                "caller": {
                    "attachment": outcome["attachment"],
                    "conversation": str(task_id),
                    "author": attacher.actor.handle,
                },
            },
            timeout=CHECKOUT_TIMEOUT_MS / 1000 + 30,
        )
        checkout_answered(answer)
    except Exception:
        logger.warning("bringing task %s's machine back failed", task_id, exc_info=True)
        failure = UNAVAILABLE
    finally:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            await db.execute(
                update(Environment)
                .where(Environment.id == environment_id)
                .values(preparing_until=None, failure=failure)
            )
            await db.commit()
