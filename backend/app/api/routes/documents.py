"""文档 (documents) — human REST front door. Phase-A contract over the mock store.

Documents are editable state: a node's content changes in place (unlike append-only
chat). Editing a document is how an actor gives the AI an instruction.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.workspace.actor import ok, resolve_actor
from app.domain.workspace.mock_store import STORE
from app.domain.workspace.schemas import (
    CreateDocNodeRequest,
    CreateDocumentRequest,
    EditDocNodeRequest,
)

router = APIRouter(tags=["Workspace · 文档"])


@router.get("/projects/{projectId}/documents", summary="List documents")
async def list_documents(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    docs = STORE.list_documents(project_id)
    return ok({"documents": [d.model_dump(by_alias=True) for d in docs]})


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
    actor = await resolve_actor(db, auth.user_id)
    doc = STORE.create_document(project_id, actor, payload.title, payload.parent_id)
    return ok({"document": doc.model_dump(by_alias=True)}, code=201, message="Created")


@router.get("/documents/{documentId}", summary="Get document tree")
async def get_document(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    return ok(STORE.get_document(document_id).model_dump(by_alias=True))


@router.post(
    "/documents/{documentId}/nodes", summary="Add document node", status_code=status.HTTP_201_CREATED
)
async def create_node(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    payload: CreateDocNodeRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    node = STORE.create_node(
        document_id, actor, payload.kind, payload.content, payload.struct_parent_id, payload.order
    )
    return ok({"node": node.model_dump(by_alias=True)}, code=201, message="Created")


@router.put("/documents/{documentId}/nodes/{nodeId}", summary="Edit document node")
async def edit_node(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    node_id: Annotated[int, Path(ge=1, alias="nodeId")],
    payload: EditDocNodeRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    node = STORE.edit_node(document_id, node_id, actor, payload.content)
    return ok({"node": node.model_dump(by_alias=True)})


@router.delete(
    "/documents/{documentId}/nodes/{nodeId}",
    summary="Delete document node",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_node(
    document_id: Annotated[int, Path(ge=1, alias="documentId")],
    node_id: Annotated[int, Path(ge=1, alias="nodeId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> None:
    STORE.delete_node(document_id, node_id)
