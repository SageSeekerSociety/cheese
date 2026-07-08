"""Agent-facing document CRUD under ``/connector`` (知是 2.0).

The human document API (``documents.py``) authenticates with a human JWT
(``require_auth_user``), so agents — who authenticate with a device + screen token —
cannot use it. This mirrors the same ``DocumentService`` behind the connector's actor
auth (``get_current_user_id`` resolves the agent's user id), so an agent can read and
write project documents exactly like a human — the medium 知是 2.0 agents use for
high-bandwidth collaboration ("一套业务服务,三个前门"). The service authorizes every
call against project membership, so a valid token is necessary but not sufficient.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.auth import get_current_user_id
from app.domain.document.schemas import (
    CreateDocumentRequest,
    SaveBodyRequest,
    UpdateDocumentRequest,
)
from app.domain.document.services import DocumentService


def build_document_connector_router(
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/connector", tags=["connector"])

    @router.get("/projects/{project_id}/documents", summary="List document tree (agent)")
    async def list_documents(
        project_id: Annotated[int, Path(ge=1)], user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        async with session_factory() as session:
            tree = await DocumentService(session).list_tree(project_id, user_id)
        return {"documents": [n.model_dump(by_alias=True) for n in tree]}

    @router.post("/projects/{project_id}/documents", summary="Create document (agent)")
    async def create_document(
        project_id: Annotated[int, Path(ge=1)],
        payload: CreateDocumentRequest,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        async with session_factory() as session:
            doc = await DocumentService(session).create_document(
                project_id, user_id, payload.title, payload.parent_id
            )
            await session.commit()
        return {"document": doc.model_dump(by_alias=True)}

    @router.get("/documents/{document_id}", summary="Get document meta + body + nodes (agent)")
    async def get_document(
        document_id: Annotated[int, Path(ge=1)], user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        async with session_factory() as session:
            detail = await DocumentService(session).get_document(document_id, user_id)
        return detail.model_dump(by_alias=True)

    @router.put("/documents/{document_id}", summary="Save document body (agent)")
    async def save_document_body(
        document_id: Annotated[int, Path(ge=1)],
        payload: SaveBodyRequest,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        async with session_factory() as session:
            detail = await DocumentService(session).save_body(document_id, user_id, payload.content)
            await session.commit()
        return detail.model_dump(by_alias=True)

    @router.patch("/documents/{document_id}", summary="Rename / move / archive document (agent)")
    async def update_document(
        document_id: Annotated[int, Path(ge=1)],
        payload: UpdateDocumentRequest,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        fields = payload.model_dump(exclude_unset=True)
        async with session_factory() as session:
            doc = await DocumentService(session).update_document(document_id, user_id, fields)
            await session.commit()
        return {"document": doc.model_dump(by_alias=True)}

    return router
