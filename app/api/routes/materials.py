from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, UnprocessableEntityError
from app.db.session import get_db
from app.domain.materials.repositories import MaterialRepository
from app.domain.materials.services import MaterialService


router = APIRouter(prefix="/materials", tags=["Materials"])

VALID_TYPES = {"image", "video", "audio", "file"}

TYPE_MIME_PREFIXES = {
    "image": ["image/"],
    "video": ["video/"],
    "audio": ["audio/"],
    "file": ["application/", "text/"],
}


async def get_material_service(db=Depends(get_db)) -> MaterialService:
    repo = MaterialRepository(session=db)
    return MaterialService(repo=repo)


@router.post(
    "",
    summary="Upload Material",
)
async def upload_material(
    file: UploadFile = File(...),
    type: str = Form(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: MaterialService = Depends(get_material_service),
) -> dict:
    if type not in VALID_TYPES:
        raise BadRequestError(f"Invalid type: {type}")

    file_content = await file.read()
    file_name = file.filename or "unnamed"
    file_mime = file.content_type or "application/octet-stream"

    valid_prefixes = TYPE_MIME_PREFIXES.get(type, [])
    if not any(file_mime.startswith(prefix) for prefix in valid_prefixes):
        raise UnprocessableEntityError(f"MIME type {file_mime} does not match type {type}")

    url = f"/uploads/{file_name}"

    result = await service.create_material(
        type=type,
        url=url,
        name=file_name,
        uploader_id=auth_user.user_id,
        meta={"size": len(file_content), "mimeType": file_mime},
    )
    return {"code": 200, "message": "Material upload successfully", "data": result}


@router.get(
    "/{material_id}",
    summary="Get Material Detail",
)
async def get_material_detail(
    material_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: MaterialService = Depends(get_material_service),
) -> dict:
    material = await service.get_material(material_id)
    return {"code": 200, "message": "Get Material successfully", "data": {"material": material}}


@router.delete(
    "/{material_id}",
    summary="Delete Material",
    status_code=204,
)
async def delete_material(
    material_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: MaterialService = Depends(get_material_service),
) -> None:
    await service.delete_material(material_id=material_id, user_id=auth_user.user_id)
