"""素材：上传一份文件，再按 id 读回去。

**读这一侧多了一道闩**（#944）。这道门（``GET /materials/{material_id}``）从前只
要求登录，返回体里带着 ``url`` —— 而 ``url`` 是 ``/uploads/...`` 下一条公开可猜的
路径（见 ``routes/uploads.py`` 顶部）。把某份素材放进题目板的「仅管理员」档之后，
那道门仍然会把它的公开路径发出去，档位就只剩一个标签。所以这里问一句
``SpaceMaterialService.may_read_outside_space``：只有真的挂在某块板的仅管理员档里
的素材才被挡住，别的素材一个字不变。

缺口仍在，没有被这道闩修好：素材表只有上传者、没有归属，所以「所有成员」档与任何
散件今天依然是「登录就读得到」。那张表和 ``attachment`` 记的是同一个缺口。
"""

import io
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.errors import BadRequestError, ForbiddenError, UnprocessableEntityError
from app.core.sentences import say
from app.core.storage import generate_storage_key, get_storage_backend
from app.db.session import get_db
from app.domain.materials.repositories import MaterialRepository
from app.domain.materials.services import MaterialService
from app.domain.space.material_service import SpaceMaterialService

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

    # 素材的上限与附件同口径（`settings.attachment_max_bytes`，默认 100MB）：先限读，
    # 读回来超了就直接拒，别把整份 body 读进内存再判。多读的那一个字节用来区分
    # 「刚好到上限」和「超了」，读到的长度也只到这里。
    file_content = await file.read(settings.attachment_max_bytes + 1)
    if len(file_content) > settings.attachment_max_bytes:
        raise UnprocessableEntityError(
            say("fileTooLarge", mb=settings.attachment_max_bytes // (1024 * 1024))
        )
    file_name = file.filename or "unnamed"
    file_mime = file.content_type or "application/octet-stream"

    valid_prefixes = TYPE_MIME_PREFIXES.get(type, [])
    if not any(file_mime.startswith(prefix) for prefix in valid_prefixes):
        raise UnprocessableEntityError(
            f"MIME type {file_mime} does not match type {type}"
        )

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
    db=Depends(get_db),
) -> dict:
    # 「仅管理员」那一档的闩。这道门只要求登录，返回体里又带着素材的 ``url`` ——
    # 一条公开可猜的 ``/uploads/...`` 路径（见 ``routes/uploads.py`` 顶部）。素材
    # 一旦进了某块板的仅管理员档，把 url 发出去就等于把文件发出去，档位也就只剩下
    # 一个标签。判据在 ``app.domain.space.material_service.may_read_outside_space``：
    # 只对真的进了那一档的素材说不，其余素材一个字不变。
    gate = SpaceMaterialService(session=db, storage=get_storage_backend())
    if not await gate.may_read_outside_space(
        material_id=material_id, user_id=auth_user.user_id
    ):
        raise ForbiddenError("This material is only visible to the board's managers")
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
