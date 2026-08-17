"""Expert role catalog + custom role CRUD (spec §8.2).

GET merges the built-in file library (app/domain/agent/role_library/*.md,
Claude Code agents format) with custom roles from the DB. Custom roles shadow
built-ins by name; built-ins are read-only (no PUT/DELETE).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.domain.expert_role.schemas import RoleCreate, RoleUpdate
from app.domain.expert_role.services import CustomRoleService

router = APIRouter(prefix="/roles", tags=["roles"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def list_roles(db: DbSession) -> dict:
    items = await CustomRoleService(db).list_merged()
    return ok(page([r.model_dump(mode="json") for r in items], len(items)))


@router.post("")
async def create_role(body: RoleCreate, db: DbSession) -> dict:
    service = CustomRoleService(db)
    role = await service.create(
        name=body.name,
        title=body.title,
        description=body.description,
        body=body.body,
        space_id=body.space_id,
        created_by=body.created_by,
    )
    return ok(service.to_out(role).model_dump(mode="json"))


@router.put("/{name}")
async def update_role(name: str, body: RoleUpdate, db: DbSession) -> dict:
    service = CustomRoleService(db)
    role = await service.update(
        name, title=body.title, description=body.description, body=body.body
    )
    return ok(service.to_out(role).model_dump(mode="json"))


@router.delete("/{name}")
async def delete_role(name: str, db: DbSession) -> dict:
    await CustomRoleService(db).delete(name)
    return ok({"deleted": True})
