"""Project machine routes — a project's compute, provisioned from MicroCloud."""

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
from app.domain.identity.actor import Actor
from app.domain.machine.limits import get_machine_limit
from app.domain.machine.microcloud import MicroCloudError
from app.domain.machine.models import MachineStatus
from app.domain.machine.schemas import MachineCreate, MachineOut
from app.domain.machine.services import MachineService
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
    mutate: bool,
) -> Actor:
    """Authorize the participant before exposing or spending team compute.

    Machine reads contain the private address of provisioned infrastructure, and
    creates/deletes mutate a prepaid MicroCloud account. Team members may inspect
    their shared pool; only team owners/admins may spend or destroy it. Agent
    identities need the same team standing.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to manage project machines")

    if mutate:
        await MachineService(db).require_create_authority(project_id, actor)
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
    """The project's machines, with status refreshed from MicroCloud."""
    await _require_project_access(project_id, db, resolver, mutate=False)
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


@router.post("/{project_id}/machines")
async def create_machine(
    project_id: uuid.UUID,
    body: MachineCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Provision a machine for this project.

    Asynchronous: returns immediately with a transitional status. Poll the list
    endpoint until it reaches `running` (or `error`).
    """
    # Provisioning spends a project's money and leaves a machine running, so —
    # unlike the read paths — an unverified handle is not good enough.
    who = await _require_project_access(project_id, db, resolver, mutate=True)

    service = _service(db)
    try:
        machine = await service.provision(
            project_id=project_id,
            requested_by=who.handle,
            # Enrollment happens later, in the enrollment sweep, long after this
            # request returned — so the device's future owner is recorded now.
            owner_user_id=who.user_id,
            ssh_pubkey=body.ssh_pubkey,
            login_user=body.login_user,
            cores=body.cores,
            memory_mb=body.memory_mb,
            disk_gb=body.disk_gb,
        )
    except MicroCloudError as exc:
        raise ValidationError(f"MicroCloud rejected the request: {exc}") from exc
    await db.commit()
    return ok(MachineOut.model_validate(machine).model_dump(mode="json"))


@router.delete("/{project_id}/machines/{machine_row_id}")
async def delete_machine(
    project_id: uuid.UUID,
    machine_row_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Destroy the machine. Asynchronous — it reports `deleting` until MicroCloud
    has torn it down, at which point the next read drops it."""
    await _require_project_access(project_id, db, resolver, mutate=True)

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
        return ok(None)
    await db.commit()
    return ok(MachineOut.model_validate(machine).model_dump(mode="json"))


@router.post("/{project_id}/machines/{machine_row_id}/{operation}")
async def change_machine_power(
    project_id: uuid.UUID,
    machine_row_id: uuid.UUID,
    operation: Literal["suspend", "resume"],
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    await _require_project_access(project_id, db, resolver, mutate=True)
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
    return ok(MachineOut.model_validate(machine).model_dump(mode="json"))
