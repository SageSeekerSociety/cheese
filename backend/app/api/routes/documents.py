"""项目文档树 (documents) — human/agent REST 前门(万物皆块之上的真实实现)。

文档是可编辑状态:节点内容就地更新(与追加式的聊天不同)。编辑文档就是 actor 向 AI
下达指令(save_body 会追加一个 EVENT 块)。所有路由把从 Bearer JWT 注入的 actor
(`require_auth_user`,绝不从请求体读取)传给 DocumentService,由 service 校验项目成员
身份。响应统一为 {code, message, data}。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.document.schemas import (
    CreateDocumentRequest,
    SaveBodyRequest,
    UpdateDocumentRequest,
)
from app.domain.document.services import DocumentService

router = APIRouter(tags=["Workspace · 文档"])


def _ok(data: object, *, code: int = 200, message: str = "success") -> dict:
    return {"code": code, "message": message, "data": data}


@router.get("/projects/{projectId}/documents", summary="List document tree")
async def list_documents(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tree = await DocumentService(db).list_tree(project_id, auth.user_id)
    return _ok({"documents": [n.model_dump(by_alias=True) for n in tree]})


@router.post(
    "/projects/{projectId}/documents",
    summary="Create document",
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    payload: CreateDocumentRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    doc = await DocumentService(db).create_document(
        project_id, auth.user_id, payload.title, payload.parent_id
    )
    return _ok({"document": doc.model_dump(by_alias=True)}, code=201, message="Created")


@router.get("/documents/{documentId}", summary="Get document meta + body + node tree")
async def get_document(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    detail = await DocumentService(db).get_document(document_id, auth.user_id)
    return _ok(detail.model_dump(by_alias=True))


@router.put("/documents/{documentId}", summary="Save document body (reconcile + event)")
async def save_document_body(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    payload: SaveBodyRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    detail = await DocumentService(db).save_body(document_id, auth.user_id, payload.content)
    return _ok(detail.model_dump(by_alias=True))


@router.patch("/documents/{documentId}", summary="Rename / move / archive document")
async def update_document(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    payload: UpdateDocumentRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    fields = payload.model_dump(exclude_unset=True)
    doc = await DocumentService(db).update_document(document_id, auth.user_id, fields)
    return _ok({"document": doc.model_dump(by_alias=True)})


@router.delete(
    "/documents/{documentId}",
    summary="Delete document (reparent children)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await DocumentService(db).delete_document(document_id, auth.user_id)
