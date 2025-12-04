from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo


router = APIRouter(prefix="/material-bundles", tags=["MaterialBundles"])


@router.get(
    "",
    summary="List Material Bundles",
)
async def list_material_bundles(
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (authorization, current_user_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"bundles": []},
    }

