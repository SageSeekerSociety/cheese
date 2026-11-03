import asyncio
import hashlib
import os
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

import aiofiles
import aiofiles.os
from fastapi import APIRouter, Depends, File, Header, Path, Query, Response, UploadFile

from app.api.conditional import if_none_match_hits
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core import raster_images
from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError, UnprocessableEntityError
from app.core.sentences import say, size_text
from app.core.srgb_images import to_srgb
from app.db.session import get_db
from app.domain.avatars.repositories import AvatarRepository
from app.domain.avatars.services import AvatarService

router = APIRouter(prefix="/avatars", tags=["Avatars"])

AVATAR_STORAGE_DIR = os.path.join(
    os.path.abspath(settings.storage_local_path), "avatars"
)


def _sniff_media_type(content: bytes) -> str:
    """Guess a media type from the leading bytes of a stored avatar.

    Avatars are stored as opaque blobs and no content type is recorded
    anywhere, so the only honest source for one is the file's own magic bytes.
    Anything we cannot recognise is served as an opaque download rather than
    mislabelled: claiming ``image/png`` for bytes that are not a PNG is what
    made a broken avatar look like a working one.
    """
    return raster_images.sniff(content) or "application/octet-stream"


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


async def _convert_stored_avatar(avatar_id: int, content: bytes) -> bytes:
    """``content`` with any wide gamut converted to sRGB, stored back once.

    An avatar stored before upload started converting them still carries
    whatever profile the phone wrote. Served as it is, that picture makes the
    browser render the page in its gamut, and some phones tint the whole screen
    for as long as it is on it. The first request heals it, and the converted
    picture is written back so the decode is paid once. A picture already in
    sRGB comes back as it arrived.
    """
    converted = await asyncio.to_thread(to_srgb, content)
    if converted is content:
        return content
    file_path = os.path.join(AVATAR_STORAGE_DIR, f"{avatar_id}")
    temporary = f"{file_path}.{os.urandom(8).hex()}.tmp"
    try:
        async with aiofiles.open(temporary, "wb") as f:
            await f.write(converted)
        # Rename, so a request reading at the same moment sees either the old
        # file or the new one, never half of either.
        os.replace(temporary, file_path)
    except OSError:
        # Serving the converted bytes already fixes the screen; storing them
        # only saves the next request the work.
        pass
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return converted


def _stored_avatar_response(
    content: bytes,
    *,
    name: str,
    created_at: datetime | None,
    if_none_match: str | None = None,
) -> Response:
    """Build the cacheable response for an avatar we actually have on disk.

    The one-year ``max-age`` lives here and only here: a client that caches a
    placeholder or an error for a year cannot be fixed by fixing the server.

    ``If-None-Match`` is answered with **304**, and that is the whole point of
    sending an ``ETag`` at all. The header was computed and returned for a long
    time while nothing ever read it back, so a client that did revalidate (hard
    reload, empty cache eviction, a new device with a shared cache) was sent the
    bytes again every time — the ETag was decoration. The file is read either
    way, because the tag *is* the hash of the bytes: not reading it would mean
    answering "unchanged" for a file we never looked at.
    """
    etag = hashlib.md5(content).hexdigest()
    last_modified = (
        created_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        if created_at
        else datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    )
    cache_headers = {
        "Cache-Control": "public, max-age=31536000",
        "ETag": f'"{etag}"',
        "Last-Modified": last_modified,
    }
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers=cache_headers)
    return Response(
        content=content,
        media_type=_sniff_media_type(content),
        headers={
            **cache_headers,
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(name, safe='')}",
        },
    )


async def get_avatar_service(db=Depends(get_db)) -> AvatarService:
    repo = AvatarRepository(session=db)
    return AvatarService(repo=repo)


@router.post("", summary="Upload Avatar", status_code=201, dependencies=[ROUTE_DECIDES])
async def create_avatar(
    avatar: UploadFile = File(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    # 上限与附件同口径（`settings.attachment_max_bytes`，默认 100MB）：限读再拒。
    file_content = await avatar.read(settings.attachment_max_bytes + 1)
    if len(file_content) > settings.attachment_max_bytes:
        raise UnprocessableEntityError(
            say("fileTooLarge", size=size_text(settings.attachment_max_bytes))
        )
    file_name = avatar.filename or "avatar"
    # Stored bytes are served to every screen that shows this avatar. A picture
    # kept in whatever gamut the phone saved it in makes the browser render the
    # page in that gamut, and the whole screen shifts colour with it on some
    # phones. Already-sRGB pictures come back untouched.
    file_content = await asyncio.to_thread(to_srgb, file_content)

    await aiofiles.os.makedirs(AVATAR_STORAGE_DIR, exist_ok=True)

    result = await service.create_avatar(url="", name=file_name, avatar_type="upload")
    avatar_id = result["avatarId"]

    file_path = os.path.join(AVATAR_STORAGE_DIR, f"{avatar_id}")
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(file_content)

    return ok(result, "Upload avatar successfully")


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
    return ok({"avatarIds": avatar_ids}, "Get available avatarIds successfully")


@router.get(
    "/default",
    summary="Get Default Avatar",
)
async def get_default_avatar(
    service: AvatarService = Depends(get_avatar_service),
    if_none_match: Annotated[str | None, Header()] = None,
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
    content = await _convert_stored_avatar(avatar.id, content)

    return _stored_avatar_response(
        content,
        name=avatar.name,
        created_at=avatar.created_at,
        if_none_match=if_none_match,
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
    return ok({"avatarId": avatar_id})


@router.get(
    "/predefined/id",
    summary="Get Predefined Avatar IDs",
)
async def get_predefined_avatar_ids(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AvatarService = Depends(get_avatar_service),
) -> dict:
    avatar_ids = await service.list_predefined_ids()
    return ok({"avatarIds": avatar_ids})


@router.get(
    "/{avatar_id}",
    summary="Get Avatar By ID",
)
async def get_avatar_by_id(
    avatar_id: Annotated[int, Path(ge=0)],
    service: AvatarService = Depends(get_avatar_service),
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    avatar = await service.get_avatar_raw(avatar_id)
    if avatar is None:
        raise NotFoundError("Avatar not found", data={"id": avatar_id})

    content = await _read_avatar_file(avatar_id)
    if content is None:
        raise NotFoundError(
            "Avatar file is missing from storage", data={"id": avatar_id}
        )
    content = await _convert_stored_avatar(avatar_id, content)

    return _stored_avatar_response(
        content,
        name=avatar.name,
        created_at=avatar.created_at,
        if_none_match=if_none_match,
    )
