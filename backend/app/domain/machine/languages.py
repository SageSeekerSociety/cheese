"""Where a person's language server in a task runs, without bringing anything up.

A language server answers questions about the task's files from beside its
working copy (`runtime_language`): it runs on the machine that holds the copy
now, in the person's name as a shell of theirs does (`terminals`), and only
there. Asking for one is not asking for the machine: when no machine holds
the copy, awake, there is no server — nothing is placed, installed, thawed or
started for it, and the sandbox's idle clock is not moved
(`task_machine.prepare` and `preview_wake.wake_for_files` are what a person
using the machine goes through; this goes through neither).

The person still needs their attachment there, for the name the server runs
under. One who has none is attached as their first shell attaches them
(`task_machine.attach`), and only when that lands on the environment already
holding the copy; any other outcome is rolled back.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BaseError
from app.domain.environment import services as environment_rows
from app.domain.environment.models import READY
from app.domain.identity.actor import Actor
from app.domain.machine import task_machine, terminals
from app.domain.machine.models import SandboxHome


async def _held_awake(db: AsyncSession, task_id: uuid.UUID) -> uuid.UUID | None:
    """The environment that holds the task's copy on a machine that is up
    now: ready, and its sandbox neither frozen nor stopped. None otherwise."""
    environment = await environment_rows.environment_of_conversation(db, task_id)
    if environment is None:
        return None
    target = await environment_rows.view(db, environment.id)
    if target is None or target.get("status", READY) != READY:
        return None
    if environment.sandbox_home_id is not None:
        home = await db.get(SandboxHome, environment.sandbox_home_id)
        if home is None or home.frozen_at is not None or home.stopped_at is not None:
            return None
    return environment.id


async def place(
    db: AsyncSession,
    task_id: uuid.UUID,
    actor: Actor,
    *,
    user_id: int,
    in_workspace: bool,
) -> terminals.Shells | None:
    """Where this person's language server in the task runs, as their shells
    would (`terminals.shells_of`), when a machine holds the task's copy awake
    now; None otherwise. Leaves the session in a transaction the caller
    commits."""
    held = await _held_awake(db, task_id)
    if held is None:
        return None

    async def found() -> terminals.Shells | None:
        return await terminals.shells_of(
            db,
            task_id,
            user_id=user_id,
            handle=actor.handle,
            in_workspace=in_workspace,
        )

    there = await found()
    if there is not None:
        return there
    try:
        _, attached = await task_machine.attach(db, task_id, actor)
    except BaseError:  # the project starts no sandbox for them
        await db.rollback()
        return None
    if attached != held:
        # Attaching would have put them somewhere new: nothing of it stays.
        await db.rollback()
        return None
    return await found()
