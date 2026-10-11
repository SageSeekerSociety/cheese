"""Each agent's execution environment in a conversation: what it is, what a new
agent gets, which sandbox specs a project is offered, and the device a sandbox
of a person's spec is placed on (``agent.environment``).

Changing one agent's environment is ``session_work.switch_environment``; the
machine it then works on is settled on its next tool call (``session_work``).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, UnprocessableEntityError
from app.core.sentences import notice_keys, say
from app.domain.agent.environment import (
    STANDARD,
    VM,
    Sandbox,
    SelfHosted,
    default_environment,
    devices_of,
    project_default,
    spec_of,
    stored,
    works_tasks_of,
)
from app.domain.agent.market import cloud_provisionable, cloud_vm_provisionable
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.user.models import User


@dataclass(frozen=True)
class Default:
    """What a new agent in a conversation gets, and where that came from."""

    environment: Sandbox | SelfHosted
    #: "task" | "room" | "project" | "deployment"
    source: str


def default_for(topic, task, project_settings: dict | None) -> Default:
    """The task's default, else its room's, else the project's, else a
    standard sandbox."""
    if task is not None and (own := stored(task.default_environment)) is not None:
        return Default(own, "task")
    if (room := stored(topic.default_environment)) is not None:
        return Default(room, "room")
    if (project := project_default(project_settings)) is not None:
        return Default(project, "project")
    return Default(default_environment(), "deployment")


async def environment_of(
    db: AsyncSession, row, topic, task, project
) -> Sandbox | SelfHosted:
    """The environment of the agent whose session is ``row``, in its
    conversation. The first time it is asked, the default there is written as
    the agent's own, so a later change of default moves nobody."""
    sessions = AgentSessionService(db)
    own = await sessions.environment(row.conversation_id, row.agent_handle)
    if own is not None:
        return stored(own.environment) or default_environment()
    chosen = default_for(topic, task, project.settings if project else None)
    written = await sessions.set_environment(
        row.conversation_id,
        row.agent_handle,
        chosen.environment.model_dump(),
        chosen_by=None,
        only_if_unset=True,
    )
    return stored(written.environment) or default_environment()


async def offered_specs(db: AsyncSession, project) -> list[dict]:
    """The sandbox specs this project may choose, each with the words a
    reader sees for it: the platform's machines where this deployment has
    them, and each person's own computers attached to the project that can
    make a sandbox."""
    out = []
    if cloud_provisionable(settings):
        out.append(_spec(STANDARD, say("specStandard")))
    if cloud_vm_provisionable(settings):
        out.append(_spec(VM, say("specVm")))
    for user_id in await _people_with_sandboxes(db, project.id):
        user = await db.get(User, user_id)
        if user is not None:
            out.append(
                _spec(devices_of(user_id), say("specDevicesOf", name=user.username))
            )
    return out


def _spec(spec: str, label) -> dict:
    return {"spec": spec, "label": str(label), **notice_keys(label=label)}


async def _people_with_sandboxes(db: AsyncSession, project_id: uuid.UUID) -> list[int]:
    """The owners of the project's own computers that can make a sandbox, in
    the order their first such computer joined."""
    devices = sql_device_service(db)
    mine = [
        device
        for device in await devices.list_devices_for_project(project_id)
        if device.supply is Supply.self_hosted
    ]
    refusing = await devices.refusing_sandboxes([d.device_id for d in mine])
    owners: list[int] = []
    for device in mine:
        if device.device_id not in refusing and device.owner_user_id not in owners:
            owners.append(device.owner_user_id)
    return owners


async def check_allowed(
    db: AsyncSession,
    project,
    environment: Sandbox | SelfHosted,
    actor: Actor,
    *,
    task=None,
) -> None:
    """Refuse an environment ``actor`` may not give: a spec this project is
    not offered, or a machine to run on without isolation that is not the
    signed-in person's own (an agent may never choose one). In a task, a
    person's own computer works only that person's tasks."""
    if isinstance(environment, Sandbox):
        if environment.spec not in {
            s["spec"] for s in await offered_specs(db, project)
        }:
            raise UnprocessableEntityError(say("environmentSpecNotOffered"))
        return
    device = await sql_device_service(db).get_device(environment.device_id)
    if (
        device is None
        or device.supply is not Supply.self_hosted
        or not await sql_device_service(db).serves_project(device.device_id, project.id)
    ):
        raise UnprocessableEntityError(say("deviceNotInProject"))
    if actor.via != "token" or actor.user_id != device.owner_user_id:
        raise ForbiddenError(say("selfHostedOwnerOnly"))
    if task is not None and not await works_tasks_of(
        db, device.device_id, project, task.owner_handle
    ):
        raise UnprocessableEntityError(say("deviceOthersOwn"))


async def device_for_spec(
    db: AsyncSession,
    *,
    spec: str,
    project,
    task,
    held: str | None,
    is_online,
):
    """A device for a sandbox of a person's spec (``devices_of:<id>``): one of
    that person's computers serving the project, online, not quarantined and
    able to make a sandbox — and, for a task, allowed to work its owner's
    tasks. The one the session already holds comes first while it qualifies.
    None when there is no such device now."""
    wanted = spec_of(spec)
    assert wanted is not None and wanted.owner_user_id is not None
    devices = sql_device_service(db)
    candidates = [
        device
        for device in await devices.healthy_devices_for_project(project.id, is_online)
        if device.supply is Supply.self_hosted
        and device.owner_user_id == wanted.owner_user_id
    ]
    refusing = await devices.refusing_sandboxes([d.device_id for d in candidates])
    candidates = [d for d in candidates if d.device_id not in refusing]
    candidates.sort(key=lambda d: d.device_id != held)
    for device in candidates:
        if await devices.get_hosted_device(device.device_id) is None:
            continue
        if task is not None and not await works_tasks_of(
            db, device.device_id, project, task.owner_handle
        ):
            continue
        return device
    return None


async def usable_by_owner(db: AsyncSession, raw, project, owner_handle) -> bool:
    """Whether an environment (as stored) may work a task owned by
    ``owner_handle``: anything on the platform's machines may; a person's own
    computer, as a self-hosted machine or behind a spec of their computers,
    works only its owner's tasks unless it is shared with the team."""
    environment = stored(raw)
    if environment is None:
        return True
    if isinstance(environment, SelfHosted):
        return await works_tasks_of(db, environment.device_id, project, owner_handle)
    spec = spec_of(environment.spec)
    if spec is None or spec.platform:
        return True
    for device in await sql_device_service(db).list_devices_for_project(project.id):
        if device.owner_user_id == spec.owner_user_id and await works_tasks_of(
            db, device.device_id, project, owner_handle
        ):
            return True
    return False
