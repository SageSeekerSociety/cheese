from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile
from fastapi.responses import Response

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, UnprocessableEntityError
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.attachment.repositories import AttachmentRepository
from app.domain.attachment.services import AttachmentService


router = APIRouter(prefix="/attachments", tags=["Attachments"])

VALID_TYPES = {"image", "video", "audio", "file"}

TYPE_MIME_PREFIXES = {
    "image": ["image/"],
    "video": ["video/"],
    "audio": ["audio/"],
    "file": ["application/", "text/", "image/", "video/", "audio/"],
}


async def get_attachment_service(db=Depends(get_db)) -> AttachmentService:
    repo = AttachmentRepository(session=db)
    storage = get_storage_backend()
    return AttachmentService(repo=repo, storage=storage)


@router.post(
    "",
    summary="Upload Attachment",
    status_code=201,
)
async def upload_attachment(
    file: UploadFile = File(...),
    type: str = Form(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
) -> dict:
    if type not in VALID_TYPES:
        raise BadRequestError(f"Invalid type: {type}")

    file_mime = file.content_type or "application/octet-stream"
    if type != "file":
        valid_prefixes = TYPE_MIME_PREFIXES.get(type, [])
        if not any(file_mime.startswith(prefix) for prefix in valid_prefixes):
            raise UnprocessableEntityError(
                f"MIME type {file_mime} does not match type {type}"
            )

    attachment = await service.upload(
        file=file.file,
        filename=file.filename or "unknown",
        content_type=file.content_type,
        uploader_id=auth_user.user_id,
        attachment_type=type,
    )
    return {
        "code": 201,
        "message": "Attachment uploaded successfully",
        "data": {"id": attachment.id, "url": attachment.url, "type": attachment.type},
    }


@router.get(
    "/{attachmentId}",
    summary="Get Attachment Detail",
)
async def get_attachment_detail(
    attachmentId: int = Path(..., ge=0),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
) -> dict:
    attachment = await service.get(attachmentId)
    return {
        "code": 200,
        "message": "OK",
        "data": {"attachment": attachment.to_dict()},
    }


@router.get(
    "/{attachmentId}/download",
    summary="Download Attachment",
)
async def download_attachment(
    attachmentId: int = Path(..., ge=0),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
) -> Response:
    content, filename, content_type = await service.download(attachmentId)
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.delete(
    "/{attachmentId}",
    summary="Delete Attachment",
    status_code=204,
)
async def delete_attachment(
    attachmentId: int = Path(..., ge=0),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
) -> None:
    await service.delete(attachmentId, user_id=auth_user.user_id)
