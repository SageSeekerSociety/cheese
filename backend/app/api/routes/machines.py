"""Project machine routes — a project's compute, provisioned from MicroCloud."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ValidationError
from app.domain.machine.microcloud import MicroCloudError
from app.domain.machine.models import MachineStatus
from app.domain.machine.schemas import MachineCreate, MachineOut
from app.domain.machine.services import MachineService

router = APIRouter(prefix="/api/projects", tags=["machines"])

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


@router.get("/{project_id}/machines")
async def list_machines(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's machines, with status refreshed from MicroCloud."""
    service = _service(db)
    machines = await service.list_for_project(project_id)
    items = [MachineOut.model_validate(m).model_dump(mode="json") for m in machines]
    await db.commit()
    return ok(page(items, len(items)))


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
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not who.authenticated:
        raise AuthenticationRequiredError("Login required to provision a machine")

    service = _service(db)
    try:
        machine = await service.provision(
            project_id=project_id,
            requested_by=who.handle,
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
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not who.authenticated:
        raise AuthenticationRequiredError("Login required to destroy a machine")

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
