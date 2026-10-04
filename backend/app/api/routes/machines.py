"""Project machine routes — a project's MicroCloud machines: list, power, delete.

Machines are opened by a room's agent session (``session_work``), never here.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.machine.limits import get_machine_limit
from app.domain.machine.live import announce_changes
from app.domain.machine.microcloud import MicroCloudError
from app.domain.machine.models import MachineStatus
from app.domain.machine.schemas import MachineOut
from app.domain.machine.services import MachineService
from app.domain.machine.supply import read_supply
from app.domain.project.repositories import ProjectRepository
from app.domain.team.repositories import TeamRepository

router = APIRouter(prefix="/projects", tags=["machines"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _service(db: AsyncSession) -> MachineService:
    service = MachineService(db)
    if not service.available:
        # A deployment with no MicroCloud credentials should say so plainly
        # rather than fail deep inside a provider call.
        raise ValidationError(
            "machine provisioning is not configured for this deployment"
        )
    return service


async def _require_project_access(
    project_id: uuid.UUID,
    db: AsyncSession,
    resolver: ActorResolverDep,
    *,
    action: str | None = None,
) -> Actor:
    """Authorize the participant before exposing or spending team compute.

    Machine reads contain the private address of provisioned infrastructure, and
    power changes and deletes act on a prepaid MicroCloud account. Team members
    may inspect their shared pool; only team owners/admins may change it. Agent
    identities need the same team standing.
    """
    actor = await resolver.resolve(project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to manage project machines")

    if action is not None:
        await MachineService(db).require_manage_authority(
            project_id, actor, action=action
        )
        return actor

    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")

    if actor.user_id is None:
        raise AuthenticationRequiredError(
            "A current user credential is required to manage team compute"
        )
    if not await TeamRepository(db).is_team_member(project.team_id, actor.user_id):
        # Conceal the project and its machine inventory from outsiders.
        raise NotFoundError("Project not found")
    return actor


@router.get("/{project_id}/machines")
async def list_machines(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's machines, as the background sweep last saw them."""
    await _require_project_access(project_id, db, resolver)
    service = _service(db)
    machines = await service.list_for_project(project_id)
    items = [MachineOut.model_validate(m).model_dump(mode="json") for m in machines]
    team_id = await service.quota_team_id(project_id)
    counted = await service.quota_machines(team_id)
    limit = await get_machine_limit(db, team_id)
    await db.commit()
    return ok(
        {
            **page(items, len(items)),
            "quota": {
                "team_id": team_id,
                "used": len(counted),
                "limit": limit,
                "project_used": sum(m.project_id == project_id for m in counted),
            },
        }
    )


@router.get("/{project_id}/cloud-supply")
async def cloud_supply(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """What a Cloud work computer can be asked for right now.

    Anyone who can choose the project's work computer can read it, so the form
    can show the range before a choice is saved. `selectable` is what a choice
    may hold; `provider` is MicroCloud's own offering. An unreadable offering
    comes back as `available: false` with the reason, never a guessed range.
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return ok(await read_supply())


@router.delete("/{project_id}/machines/{machine_row_id}")
async def delete_machine(
    project_id: uuid.UUID,
    machine_row_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Destroy the machine. Asynchronous — it reports `deleting` until MicroCloud
    has torn it down, at which point the machine sweep drops it."""
    await _require_project_access(
        project_id, db, resolver, action=say("machineActionDelete")
    )

    service = _service(db)
    machine = await service.get_or_404(machine_row_id)
    if machine.project_id != project_id:
        # Addressing another project's machine through this one must not work
        # just because the id was guessed right.
        raise ValidationError("machine does not belong to this project")
    try:
        machine = await service.destroy(machine)
    except MicroCloudError as exc:
        raise ValidationError(f"MicroCloud rejected the request: {exc}") from exc
    if machine.status == MachineStatus.deleted:
        await service.forget(machine)
        await db.commit()
        await announce_changes(db)
        return ok(None)
    await db.commit()
    await announce_changes(db)
    return ok(MachineOut.model_validate(machine).model_dump(mode="json"))


@router.post("/{project_id}/machines/{machine_row_id}/{operation}")
async def change_machine_power(
    project_id: uuid.UUID,
    machine_row_id: uuid.UUID,
    operation: Literal["suspend", "resume"],
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    await _require_project_access(
        project_id,
        db,
        resolver,
        action=say("machineActionSuspend")
        if operation == "suspend"
        else say("machineActionResume"),
    )
    service = _service(db)
    machine = await service.get_or_404(machine_row_id)
    if machine.project_id != project_id:
        raise ValidationError("machine does not belong to this project")
    try:
        machine = await (
            service.suspend(machine)
            if operation == "suspend"
            else service.resume(machine)
        )
    except MicroCloudError as exc:
        raise ValidationError(f"MicroCloud rejected the request: {exc}") from exc
    await db.commit()
    await announce_changes(db)
    return ok(MachineOut.model_validate(machine).model_dump(mode="json"))
