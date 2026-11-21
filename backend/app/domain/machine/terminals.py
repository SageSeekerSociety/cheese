"""A person's terminals in a task: where they run, and who may open one.

A person opens shells in the environment where the task's work is, the one
its AI teammates work in (`environment_of_conversation`), as the person's own
attachment there: what a shell runs carries the person's name
(`runtime_core.identity`) and is answered, when it asks the platform
anything, on the environment's authority — never on the person's own, so a
shell can accept no hand-in and change no setting (`machine.caller`). The
shells themselves are the executor's (`runtime_terminal`): it lists, keeps
and ends them; nothing about them is recorded here.

One environment is one trust scope. A shell runs as the same user as the
agent's commands, with the project's variables, and can do what the agent
there can. On a person's own computer that is the owner's machine and
network, so there only the computer's owner may open one; a team's shared
computer and the platform's are open to whoever may edit the task.

What these answer before any shell is reached (`Refusal`) is what the 终端
tab says instead of a shell.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.execution import executor_state
from app.domain.device.models import DeviceRow
from app.domain.environment import services as environment_rows
from app.domain.environment.models import (
    READY,
    Environment,
    EnvironmentAttachment,
)
from app.domain.team.models import Team

#: What an executor that holds shells offers (`runtime.Executor` ``ping``).
CAPABILITY = "terminal"
#: What an executor older than shells answers the request for one.
UNKNOWN_METHOD = "Unknown executor method"


class Refusal(enum.StrEnum):
    #: The environment is on someone else's own computer.
    not_owner = "not_owner"
    #: The machine cannot hold a terminal (Windows has no pseudo-terminal).
    unsupported = "unsupported"
    #: The machine's executor or connector predates terminals.
    outdated = "outdated"
    #: The person already has the most shells running there.
    limit = "limit"


@dataclass(frozen=True)
class Shells:
    """Where a person's shells in a task run."""

    device_id: str
    state: str
    #: What the executor is told about every shell of this person here:
    #: whose they are, which task's checkout they start in, and whose name
    #: their commands carry.
    params: dict


def _refused_on(target: str, device_owner: int | None, user_id: int) -> Refusal | None:
    if target.startswith("windows"):
        return Refusal.unsupported
    if device_owner is not None and device_owner != user_id:
        return Refusal.not_owner
    return None


async def _personal_owner(db: AsyncSession, environment: Environment) -> int | None:
    """Whose own computer the environment is on; None on a team's shared
    computer, on the platform's, or on none yet."""
    if environment.device_id is None or environment.executor.get("platform"):
        return None
    return await db.scalar(
        select(Team.personal_owner_user_id)
        .join(DeviceRow, DeviceRow.team_id == Team.id)
        .where(DeviceRow.device_id == environment.device_id)
    )


async def refusal(
    db: AsyncSession, task_id: uuid.UUID, user_id: int, *, hub
) -> Refusal | None:
    """Why this person may not open a terminal in the task's environment as
    it stands, if they may not: its machine is someone else's own computer,
    or one that cannot hold a terminal (as far as ``hub`` knows what it
    runs)."""
    environment = await environment_rows.environment_of_conversation(db, task_id)
    if environment is None:
        return None
    target = hub.target(environment.device_id) if environment.device_id else ""
    return _refused_on(target, await _personal_owner(db, environment), user_id)


async def shells_of(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    user_id: int,
    handle: str,
    in_workspace: bool,
) -> Shells | None:
    """Where this person's shells in the task run: the environment the task
    points to, on a machine that holds it now, with the person attached to
    it (`task_machine.prepare` attaches them). None while that is not so."""
    environment = await environment_rows.environment_of_conversation(db, task_id)
    if environment is None:
        return None
    target = await environment_rows.view(db, environment.id)
    if (
        target is None
        or target.get("status", READY) != READY
        or not environment_rows.holds_a_copy(target)
    ):
        return None
    attachment = await db.scalar(
        select(EnvironmentAttachment.id).where(
            EnvironmentAttachment.environment_id == environment.id,
            EnvironmentAttachment.user_id == user_id,
        )
    )
    if attachment is None:
        return None
    return Shells(
        device_id=target["device_id"],
        state=executor_state(target),
        params={
            "owner": str(user_id),
            "task_id": str(task_id),
            "in_workspace": in_workspace,
            "caller": {
                "attachment": str(attachment),
                "conversation": str(task_id),
                "author": handle,
            },
        },
    )
