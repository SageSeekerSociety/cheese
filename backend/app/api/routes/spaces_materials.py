"""一块题目板的共用资料库 (#944)：板子上的文件，两条可见性档位。

五条路由，一个概念 —— 这块板上有哪些资料、谁能看：

- ``GET /spaces/{spaceId}/materials`` 清单。成员看得到「所有成员」那一档，
  管理员两档都看得到；响应里另外带一个 ``canManage``，界面拿它决定摆不摆那一组
  管理入口，以及每一行带一个 ``usedByCount``（被几处教学配置引用）—— 后者只发给
  能删的人，见 ``list_space_materials``。
- ``POST /spaces/{spaceId}/materials`` 传一份上去（multipart），同时定档。
- ``PATCH /spaces/{spaceId}/materials/{materialId}`` 改档。
- ``DELETE /spaces/{spaceId}/materials/{materialId}`` 从板上撤下来（软删关联行）。
- ``GET /spaces/{spaceId}/materials/{materialId}/download`` 取字节。资料库页面上
  点开预览也走这一条，带 ``preview=true``：看一眼不算一次下载。

三条判据全在 ``app.domain.space.material_service`` 里，这一层只搬数据、拼响应。
**响应里从不出现素材的 ``url``** —— 那是 ``/uploads/...`` 下一条公开可猜的路径，
交出去「仅管理员」这一档就只剩一个标签；要字节就走 download 那条，它判权限。

``require_reviewed_space`` 与本文件树里其他空间路由同一道前置：未过审的板子对外
一律 404。因此路径参数必须叫 ``spaceId``（那道依赖从 ``request.path_params`` 里
按这个名字取），与外层 ``spaces.py`` 的写法一致。

这个模块不 import 任何 ``app.domain.*.models``（`.importlinter` 的 C2），也不 import
任何 repository（`tests/unit/test_domain_import_guard.py` 的棘轮）：素材那一域的行
经 ``MaterialService`` 拿，见 ``material_service.py``。
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.api.space_deps import require_reviewed_space
from app.api.write_access import Can
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say, size_text
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.space.material_service import SpaceMaterialService
from app.domain.space.permissions import Permission as SpacePermission
from app.domain.task.teaching import count_material_references
from app.domain.user.services import user_service

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)


class PatchSpaceMaterialRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    visibility: str = Field(..., description="members | admins")


async def get_space_material_service(db=Depends(get_db)) -> SpaceMaterialService:
    return SpaceMaterialService(session=db, storage=get_storage_backend())


@router.get(
    "/{spaceId}/materials",
    summary="List Space Materials",
    dependencies=[Can(SpacePermission.view)],
)
async def list_space_materials(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMaterialService = Depends(get_space_material_service),
    db: AsyncSession = Depends(get_db),
) -> dict:
    data = await service.list_for_space(space_id=space_id, user_id=auth_user.user_id)
    # 每一份是谁传的：资料库页面每行写上传人。昵称在用户那一域，这一层拼上去。
    profiles = await user_service(db).get_users_by_ids(
        sorted({item["uploaderId"] for item in data["materials"] if item["uploaderId"]})
    )
    for item in data["materials"]:
        profile = profiles.get(item["uploaderId"])
        item["uploaderName"] = profile.nickname if profile else None
    if data["canManage"]:
        # 「还有几处配置列着它」只发给能删的人：这是删除之前的判断依据（撤了就
        # 留下几处指着空处），成员那一侧既看不见管理入口，也不该看见别人的配置
        # 里有没有它。
        counts = await count_material_references(db, space_id=space_id)
        for item in data["materials"]:
            item["usedByCount"] = counts.get(item["id"], 0)
    return ok(data)


@router.post(
    "/{spaceId}/materials",
    summary="Upload Space Material",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Can(SpacePermission.manage_materials)],
)
async def upload_space_material(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    file: UploadFile = File(...),
    visibility: str | None = Form(None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMaterialService = Depends(get_space_material_service),
) -> dict:
    # 与 ``POST /materials`` 同一条路、同一个上限：``settings.attachment_max_bytes``
    # （默认 100MB，与 nginx 前门、对话里的附件同口径）。限读再拒——这道 read 在
    # ``service.add`` 里那句 ``_require_admin`` 之前，不判大小的话，一个进不了这块板
    # 的人也能先让服务器把整份 body 收进内存。
    content = await file.read(settings.attachment_max_bytes + 1)
    if len(content) > settings.attachment_max_bytes:
        raise UnprocessableEntityError(
            say("fileTooLarge", size=size_text(settings.attachment_max_bytes))
        )
    item = await service.add(
        space_id=space_id,
        user_id=auth_user.user_id,
        content=content,
        filename=file.filename or "unnamed",
        content_type=file.content_type or "application/octet-stream",
        visibility=visibility,
    )
    return ok({"material": item})


@router.patch(
    "/{spaceId}/materials/{materialId}",
    summary="Patch Space Material",
    dependencies=[Can(SpacePermission.manage_materials)],
)
async def patch_space_material(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    material_id: Annotated[int, Path(ge=0, alias="materialId")],
    payload: PatchSpaceMaterialRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMaterialService = Depends(get_space_material_service),
) -> dict:
    item = await service.set_visibility(
        space_id=space_id,
        user_id=auth_user.user_id,
        material_id=material_id,
        visibility=payload.visibility,
    )
    return ok({"material": item})


@router.delete(
    "/{spaceId}/materials/{materialId}",
    summary="Delete Space Material",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Can(SpacePermission.manage_materials)],
)
async def delete_space_material(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    material_id: Annotated[int, Path(ge=0, alias="materialId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMaterialService = Depends(get_space_material_service),
) -> None:
    await service.remove(
        space_id=space_id, user_id=auth_user.user_id, material_id=material_id
    )


@router.get(
    "/{spaceId}/materials/{materialId}/download",
    summary="Download Space Material",
    dependencies=[Can(SpacePermission.view)],
)
async def download_space_material(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    material_id: Annotated[int, Path(ge=0, alias="materialId")],
    preview: Annotated[bool, Query()] = False,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMaterialService = Depends(get_space_material_service),
) -> Response:
    content, filename, content_type = await service.download(
        space_id=space_id,
        user_id=auth_user.user_id,
        material_id=material_id,
        counted=not preview,
    )
    # ``filename*=UTF-8''…`` 而不是裸引号：中文文件名直接写进 header 会让
    # Starlette 按 latin-1 编码时报错，名字里的引号还会把 header 语法提前闭合。
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''" + quote(filename, safe="")
            )
        },
    )
