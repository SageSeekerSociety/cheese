"""Built-in starting configurations; existing agents never read this catalog."""

from dataclasses import asdict

from fastapi import APIRouter

from app.api.response import ok, page
from app.domain.agent_type.library import preset_types
from app.domain.agent_type.schemas import AgentTypeOut

router = APIRouter(prefix="/agent-types", tags=["agent-types"])


@router.get("")
async def list_agent_types() -> dict:
    items = [
        AgentTypeOut(**asdict(preset), builtin=True).model_dump(mode="json")
        for preset in preset_types().values()
    ]
    return ok(page(items, len(items)))
