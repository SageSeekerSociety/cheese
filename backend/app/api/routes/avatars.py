import hashlib
import os
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

import aiofiles
import aiofiles.os
from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.avatars.repositories import AvatarRepository
from app.domain.avatars.services import AvatarService

router = APIRouter(prefix="/avatars", tags=["Avatars"])

AVATAR_STORAGE_DIR = os.path.join(
    os.path.abspath(settings.storage_local_path), "avatars"
)

# Avatars are stored as opaque blobs and no content type is recorded anywhere,
# so the only honest source for one is the file's own magic bytes.
_MAGIC_MEDIA_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_media_type(content: bytes) -> str:
    """Guess a media type from the leading bytes of a stored avatar.

    Anything we cannot recognise is served as an opaque download rather than
    mislabelled: claiming ``image/png`` for bytes that are not a PNG is what
    made a broken avatar look like a working one.
    """
    for magic, media_type in _MAGIC_MEDIA_TYPES:
        if content.startswith(magic):
            return media_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


async def _read_avatar_file(avatar_id: int) -> bytes | None:
    """Return the stored bytes for ``avatar_id``, or ``None`` if there is no file.

    Read-and-catch rather than exists-then-read: the two-step version answers a
    question that may already be stale by the time the file is opened.
    """
    file_path = os.path.join(AVATAR_STORAGE_DIR, f"{avatar_id}")
    try:
        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def _stored_avatar_response(
    content: bytes, *, name: str, created_at: datetime | None
) -> Response:
    """Build the cacheable response for an avatar we actually have on disk.

    The one-year ``max-age`` lives here and only here: a client that caches a
    placeholder or an error for a year cannot be fixed by fixing the server.
    """
    etag = hashlib.md5(content).hexdigest()
    last_modified = (
        created_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        if created_at
        else datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    )
    return Response(
        content=content,
        media_type=_sniff_media_type(content),
        headers={
            "Cache-Control": "public, max-age=31536000",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(name, safe='')}",
            "ETag": f'"{etag}"',
            "Last-Modified": last_modified,
        },
    )


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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    file_content = await avatar.read()
    file_name = avatar.filename or "avatar"

    await aiofiles.os.makedirs(AVATAR_STORAGE_DIR, exist_ok=True)

    result = await service.create_avatar(url="", name=file_name, avatar_type="upload")
    avatar_id = result["avatarId"]

    file_path = os.path.join(AVATAR_STORAGE_DIR, f"{avatar_id}")
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(file_content)

    return {"code": 201, "message": "Upload avatar successfully", "data": result}


@router.get(
    "/",
    summary="Get Available Avatar IDs",
)
async def get_available_avatars(
    type: str = Query(default="predefined"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
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

    content = await _read_avatar_file(avatar.id)
    if content is None:
        raise NotFoundError(
            "Default avatar file is missing from storage",
            data={"id": avatar.id},
        )

    return _stored_avatar_response(
        content, name=avatar.name, created_at=avatar.created_at
    )


@router.get(
    "/default/id",
    summary="Get Default Avatar ID",
)
async def get_default_avatar_id(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    avatar_id = await service.get_default_id()
    return {"code": 200, "message": "OK", "data": {"avatarId": avatar_id}}


@router.get(
    "/predefined/id",
    summary="Get Predefined Avatar IDs",
)
async def get_predefined_avatar_ids(
    auth_user: AuthUserInfo = Depends(require_auth_user),
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

    content = await _read_avatar_file(avatar_id)
    if content is None:
        raise NotFoundError(
            "Avatar file is missing from storage", data={"id": avatar_id}
        )

    return _stored_avatar_response(
        content, name=avatar.name, created_at=avatar.created_at
    )
