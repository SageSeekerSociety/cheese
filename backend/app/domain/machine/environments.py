"""Each agent's execution environment in a conversation: what it is, what a new
agent gets, which sandbox specs a project is offered, and the team device a
sandbox is placed on (``agent.environment``).

Changing one agent's environment is ``session_work.switch_environment``; the
machine it then works on is settled on its next tool call (``session_work``).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, UnprocessableEntityError
from app.core.sentences import notice_keys, say
from app.domain.agent.environment import (
    STANDARD,
    TEAM_DEVICES,
    VM,
    Sandbox,
    SelfHosted,
    default_environment,
    project_default,
    spec_of,
    stored,
    works_tasks_of,
)
from app.domain.agent.market import cloud_provisionable, cloud_vm_provisionable
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.usage import compute


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
    instance = await db.scalar(
        select(AgentInstance).where(
            AgentInstance.project_id == topic.project_id,
            AgentInstance.handle == row.agent_handle,
        )
    )
    device = (
        (instance.configuration or {}).get("execution_device_id") if instance else None
    )
    chosen = (
        Default(Sandbox(spec=TEAM_DEVICES), "agent")
        if device
        else default_for(topic, task, project.settings if project else None)
    )
    written = await sessions.set_environment(
        row.conversation_id,
        row.agent_handle,
        chosen.environment.model_dump(),
        chosen_by=None,
        only_if_unset=True,
    )
    return stored(written.environment) or default_environment()


async def offered_specs(
    db: AsyncSession, project, *, project_devices: list | None = None
) -> list[dict]:
    """The sandbox specs this project may choose, each with the words a
    reader sees for it — its name, and what it is and what it costs: the
    platform's machines where this deployment has them, and the project's team
    devices that can make a sandbox."""
    out = []
    if cloud_provisionable(settings):
        out.append(_spec(STANDARD, say("specStandard"), _standard_about()))
    if cloud_vm_provisionable(settings):
        out.append(_spec(VM, say("specVm"), _vm_about()))
    devices = sql_device_service(db)
    mine = (
        project_devices
        if project_devices is not None
        else await devices.list_devices_for_project(project.id)
    )
    refusing = (
        await devices.refusing_sandboxes([d.device_id for d in mine]) if mine else set()
    )
    if any(d.device_id not in refusing for d in mine):
        out.append(
            _spec(TEAM_DEVICES, say("specTeamDevices"), say("specTeamDevicesAbout"))
        )
    return out


def _standard_about():
    """What a standard sandbox is, and the credits it is charged per running
    hour — the price ``usage.compute`` charges it at."""
    price = compute.hourly_price(compute.SANDBOX, compute.SANDBOX)
    if price is None:
        return say("specStandardAboutUnpriced")
    return say("specStandardAbout", credits=_number(price))


def _vm_about():
    """What a whole VM is, the size every one is created with, and the
    credits that size is charged per hour."""
    cores, memory_mb = settings.cloud_vm_cores, settings.cloud_vm_memory_mb
    price = compute.hourly_price(compute.VM, compute.vm_spec(cores, memory_mb))
    if price is None:
        return say("specVmAboutUnpriced")
    return say(
        "specVmAbout", cores=cores, memory=memory_mb // 1024, credits=_number(price)
    )


def _number(price: float) -> int | float:
    """``price`` without a trailing ``.0``: a sentence says ``5``, not ``5.0``."""
    return int(price) if price.is_integer() else price


def _spec(spec: str, label, description) -> dict:
    return {
        "spec": spec,
        "label": str(label),
        "description": str(description),
        **notice_keys(label=label, description=description),
    }


async def environment_words(db: AsyncSession, environment: Sandbox | SelfHosted):
    """What a sentence calls ``environment``: its spec's name, as the picker
    shows it, or the name of the one computer it runs on."""
    if isinstance(environment, Sandbox):
        if environment.spec == STANDARD:
            return say("specStandard")
        if environment.spec == VM:
            return say("specVm")
        return say("specTeamDevices")
    device = await sql_device_service(db).get_device(environment.device_id)
    return device.name if device is not None and device.name else environment.device_id


async def check_allowed(
    db: AsyncSession,
    project,
    environment: Sandbox | SelfHosted,
    actor: Actor,
    *,
    task=None,
) -> None:
    """Refuse an environment ``actor`` may not give: a spec this project is
    not offered, or a machine that the person is not authorized to run without isolation.
    Only administrators of the device's team can authorize that access."""
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
    if (
        actor.via != "token"
        or actor.user_id is None
        or not await sql_device_service(db).may_manage(device, actor.user_id)
    ):
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
    agent_handle: str | None = None,
):
    """A device for the team's sandbox pool: online, not quarantined and
    able to make a sandbox. Every team member's tasks can use it.
    The one the session already holds comes first while it qualifies.
    None when there is no such device now."""
    wanted = spec_of(spec)
    assert wanted is not None and not wanted.platform
    devices = sql_device_service(db)
    candidates = [
        device
        for device in await devices.healthy_devices_for_project(project.id, is_online)
        if device.supply is Supply.self_hosted
    ]
    instance = (
        await db.scalar(
            select(AgentInstance).where(
                AgentInstance.project_id == project.id,
                AgentInstance.handle == agent_handle,
            )
        )
        if agent_handle
        else None
    )
    selected = (
        (instance.configuration or {}).get("execution_device_id") if instance else None
    )
    if selected:
        candidates = [d for d in candidates if d.device_id == selected]
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
        if await works_tasks_of(db, device.device_id, project, owner_handle):
            return True
    return False
