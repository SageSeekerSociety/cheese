from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo


router = APIRouter(prefix="/avatars", tags=["Avatars"])


@router.post(
    "",
    summary="Upload Avatar",
)
async def create_avatar(
    avatar: UploadFile = File(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (avatar, authorization, current_user_id)
    avatar_id = 1
    return {
        "code": 201,
        "message": "Upload avatar successfully",
        "data": {"avatarId": avatar_id},
    }


@router.get(
    "",
    summary="Get Available Avatar IDs",
)
async def get_available_avatars(
    type: str = Query(default="predefined"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (authorization, current_user_id)
    if type != "predefined":
        raise BadRequestError("Invalid avatar type")
    return {
        "code": 200,
        "message": "Get available avatarIds successfully",
        "data": {"avatarIds": []},
    }

