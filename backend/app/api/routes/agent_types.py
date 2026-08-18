"""Agent type catalog + custom type CRUD.

GET merges the preset file library (app/domain/agent_type/presets/*.md, Claude
Code agents format) with custom types from the DB. A custom type shadows a
preset by name; presets are read-only (no PUT/DELETE).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.domain.agent_type.schemas import AgentTypeCreate, AgentTypeUpdate
from app.domain.agent_type.services import AgentTypeService

router = APIRouter(prefix="/agent-types", tags=["agent-types"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def list_agent_types(db: DbSession) -> dict:
    items = await AgentTypeService(db).list_merged()
    return ok(page([t.model_dump(mode="json") for t in items], len(items)))


@router.post("")
async def create_agent_type(body: AgentTypeCreate, db: DbSession) -> dict:
    service = AgentTypeService(db)
    agent_type = await service.create(
        name=body.name,
        title=body.title,
        description=body.description,
        body=body.body,
        skills=body.skills,
        mcp_servers=body.mcp_servers,
        model=body.model,
        effort=body.effort,
        harness=body.harness,
        space_id=body.space_id,
        created_by=body.created_by,
    )
    return ok(service.to_out(agent_type).model_dump(mode="json"))


@router.put("/{name}")
async def update_agent_type(name: str, body: AgentTypeUpdate, db: DbSession) -> dict:
    service = AgentTypeService(db)
    agent_type = await service.update(name, **body.model_dump(exclude_unset=True))
    return ok(service.to_out(agent_type).model_dump(mode="json"))


@router.delete("/{name}")
async def delete_agent_type(name: str, db: DbSession) -> dict:
    await AgentTypeService(db).delete(name)
    return ok({"deleted": True})
