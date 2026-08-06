from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, UnprocessableEntityError
from app.core.storage import generate_storage_key, get_storage_backend
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
    status_code=201,
)
async def upload_material(
    file: UploadFile = File(...),
    type: str = Form(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialService = Depends(get_material_service),
) -> dict:
    if type not in VALID_TYPES:
        raise BadRequestError(f"Invalid type: {type}")

    file_content = await file.read()
    file_name = file.filename or "unnamed"
    file_mime = file.content_type or "application/octet-stream"

    valid_prefixes = TYPE_MIME_PREFIXES.get(type, [])
    if not any(file_mime.startswith(prefix) for prefix in valid_prefixes):
        raise UnprocessableEntityError(
            f"MIME type {file_mime} does not match type {type}"
        )

    import io

    storage = get_storage_backend()
    storage_key = generate_storage_key(file_name, prefix=f"materials/{type}")
    url = await storage.upload(io.BytesIO(file_content), storage_key, file_mime)

    # Build a meta dict whose keys match the frontend's discriminated union
    # types (FileMeta / ImageMeta / VideoMeta / AudioMeta). For non-file types
    # the frontend casts meta as ImageMeta / VideoMeta / AudioMeta and reads
    # `width`/`height`/`duration`/`thumbnail` — the dimensions are placeholders
    # until proper media-probing is wired in.
    meta: dict = {
        "size": len(file_content),
        "mime": file_mime,
        # Keep storageKey + mimeType for backwards-compat with internal code
        # that may still read them.
        "mimeType": file_mime,
        "storageKey": storage_key,
    }
    if type == "file":
        meta["name"] = file_name
        # `expires` is null for now; frontend tolerates 0/null for "no expiry".
        meta.setdefault("expires", 0)

    result = await service.create_material(
        type=type,
        url=url,
        name=file_name,
        uploader_id=auth_user.user_id,
        meta=meta,
    )
    return {"code": 201, "message": "Created", "data": result}


@router.get(
    "/{material_id}",
    summary="Get Material Detail",
)
async def get_material_detail(
    material_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialService = Depends(get_material_service),
) -> dict:
    material = await service.get_material(material_id)
    return {
        "code": 200,
        "message": "Get Material successfully",
        "data": {"material": material},
    }


@router.delete(
    "/{material_id}",
    summary="Delete Material",
    status_code=204,
)
async def delete_material(
    material_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialService = Depends(get_material_service),
) -> None:
    await service.delete_material(material_id=material_id, user_id=auth_user.user_id)
