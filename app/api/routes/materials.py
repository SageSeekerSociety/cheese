from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, Path, UploadFile

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo


router = APIRouter(prefix="/materials", tags=["Materials"])


@router.post(
    "",
    summary="Upload Material",
)
async def upload_material(
    file: UploadFile = File(...),
    type: str | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (file, type, authorization, current_user_id)
    material_id = 1
    return {
        "code": 200,
        "message": "Material upload successfully",
        "data": {"id": material_id},
    }


@router.get(
    "/{material_id}",
    summary="Get Material Detail",
)
async def get_material_detail(
    material_id: int = Path(..., ge=1),
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (authorization, current_user_id)
    material = {
        "id": material_id,
        "type": "",
        "url": "",
    }
    return {
        "code": 200,
        "message": "Get Material successfully",
        "data": {"material": material},
    }

