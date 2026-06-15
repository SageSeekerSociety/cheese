"""Expert role catalog (spec §8.2)."""

from fastapi import APIRouter

from app.api.response import ok, page
from app.domain.agent.roles import PRESET_ROLES

router = APIRouter(prefix="/api/expert-roles", tags=["roles"])


@router.get("")
async def list_roles() -> dict:
    items = [
        {"name": name, "label": r["label"], "description": r["description"]}
        for name, r in PRESET_ROLES.items()
    ]
    return ok(page(items, len(items)))
