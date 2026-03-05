import hashlib
import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.avatars.repositories import AvatarRepository
from app.domain.avatars.services import AvatarService

router = APIRouter(prefix="/avatars", tags=["Avatars"])

AVATAR_STORAGE_DIR = "/tmp/cheese_avatars"


async def get_avatar_service(db=Depends(get_db)) -> AvatarService:
    repo = AvatarRepository(session=db)
    return AvatarService(repo=repo)


@router.post(
    "",
    summary="Upload Avatar",
    status_code=201,
)
async def create_avatar(
    avatar: UploadFile = File(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    file_content = await avatar.read()
    file_name = avatar.filename or "avatar"

    os.makedirs(AVATAR_STORAGE_DIR, exist_ok=True)

    result = await service.create_avatar(url="", name=file_name, avatar_type="upload")
    avatar_id = result["avatarId"]

    file_path = os.path.join(AVATAR_STORAGE_DIR, f"{avatar_id}")
    with open(file_path, "wb") as f:
        f.write(file_content)

    return {"code": 201, "message": "Upload avatar successfully", "data": result}


@router.get(
    "/",
    summary="Get Available Avatar IDs",
)
async def get_available_avatars(
    type: str = Query(default="predefined"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    if type.upper() != "PREDEFINED":
        raise BadRequestError("Invalid avatar type")
    avatar_ids = await service.list_predefined_ids()
    return {
        "code": 200,
        "message": "Get available avatarIds successfully",
        "data": {"avatarIds": avatar_ids},
    }


@router.get(
    "/default",
    summary="Get Default Avatar",
)
async def get_default_avatar(
    service: AvatarService = Depends(get_avatar_service),
) -> Response:
    avatar = await service.get_default_raw()
    if avatar is None:
        raise NotFoundError("No default avatar found")

    file_path = os.path.join(AVATAR_STORAGE_DIR, f"{avatar.id}")
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            content = f.read()
    else:
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    etag = hashlib.md5(content).hexdigest()
    last_modified = (
        avatar.created_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        if avatar.created_at
        else datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    )

    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000",
            "Content-Disposition": f'inline; filename="{avatar.name}"',
            "ETag": f'"{etag}"',
            "Last-Modified": last_modified,
        },
    )


@router.get(
    "/default/id",
    summary="Get Default Avatar ID",
)
async def get_default_avatar_id(
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    avatar_id = await service.get_default_id()
    return {"code": 200, "message": "OK", "data": {"avatarId": avatar_id}}


@router.get(
    "/predefined/id",
    summary="Get Predefined Avatar IDs",
)
async def get_predefined_avatar_ids(
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    avatar_ids = await service.list_predefined_ids()
    return {"code": 200, "message": "OK", "data": {"avatarIds": avatar_ids}}


@router.get(
    "/{avatar_id}",
    summary="Get Avatar By ID",
)
async def get_avatar_by_id(
    avatar_id: Annotated[int, Path(ge=0)],
    service: AvatarService = Depends(get_avatar_service),
) -> Response:
    avatar = await service.get_avatar_raw(avatar_id)
    if avatar is None:
        raise NotFoundError("Avatar not found", data={"id": avatar_id})

    file_path = os.path.join(AVATAR_STORAGE_DIR, f"{avatar_id}")
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            content = f.read()
    else:
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    etag = hashlib.md5(content).hexdigest()
    last_modified = (
        avatar.created_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        if avatar.created_at
        else datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    )

    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000",
            "Content-Disposition": f'inline; filename="{avatar.name}"',
            "ETag": f'"{etag}"',
            "Last-Modified": last_modified,
        },
    )
