"""Legal documents and the signed-in user's consent to them (#1486)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.db.session import get_db
from app.domain.legal.documents import (
    DOCUMENTS,
    LegalDocument,
    LegalVersion,
    sha256_of,
    text_of,
)
from app.domain.legal.services import ConsentService

router = APIRouter(tags=["Legal"])


def client_context(request: Request) -> tuple[str, str]:
    """(ip, user agent) as a consent row records them. The address is the one
    the server resolved through the proxies it trusts (FORWARDED_ALLOW_IPS),
    never X-Forwarded-For as sent: its left part is whatever the client wrote."""
    ip = request.client.host if request.client else ""
    return ip, request.headers.get("user-agent", "")


def _summary(doc: LegalDocument, version: LegalVersion) -> dict:
    return {
        "document": doc.key,
        "title": doc.title,
        "version": version.version,
        "effectiveDate": version.effective_date.isoformat(),
    }


def _document_or_404(document: str) -> LegalDocument:
    doc = DOCUMENTS.get(document)
    if doc is None:
        raise NotFoundError("No such document")
    return doc


def _full(doc: LegalDocument, version: LegalVersion) -> dict:
    return {
        **_summary(doc, version),
        "current": version == doc.current,
        "content": text_of(doc.key, version.version),
        "sha256": sha256_of(doc.key, version.version),
    }


@router.get("/legal/documents", summary="Current version of every legal document")
async def list_documents() -> dict:
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "documents": [_summary(d, d.current) for d in DOCUMENTS.values()],
        },
    }


@router.get("/legal/documents/{document}", summary="Current text of a document")
async def get_document(document: Annotated[str, Path()]) -> dict:
    doc = _document_or_404(document)
    return {"code": 200, "message": "OK", "data": _full(doc, doc.current)}


@router.get(
    "/legal/documents/{document}/versions/{version}",
    summary="Text of any published version of a document",
)
async def get_document_version(
    document: Annotated[str, Path()], version: Annotated[str, Path()]
) -> dict:
    doc = _document_or_404(document)
    found = doc.get(version)
    if found is None:
        raise NotFoundError("No such version")
    return {"code": 200, "message": "OK", "data": _full(doc, found)}


@router.get(
    "/users/me/consents",
    summary="Documents the current user still has to accept",
)
async def get_my_pending_consents(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    pending = await ConsentService(session).pending(auth_user.user_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "pending": [_summary(DOCUMENTS[k], DOCUMENTS[k].current) for k in pending]
        },
    }


class AcceptRequest(BaseModel):
    #: document key → the version the user was shown.
    documents: dict[str, str]


@router.post("/users/me/consents", summary="Accept the current legal documents")
async def accept_documents(
    payload: AcceptRequest,
    request: Request,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    service = ConsentService(session)
    for key, version in payload.documents.items():
        doc = DOCUMENTS.get(key)
        if doc is None or version != doc.current.version:
            # A dialog opened before a newer version was published: recording
            # it would say this person accepted rules they never saw.
            raise UnprocessableEntityError("协议已更新，请刷新页面后重新阅读")
    pending = await service.pending(auth_user.user_id)
    missing = [k for k in pending if k not in payload.documents]
    if missing:
        titles = "、".join(f"《{DOCUMENTS[k].title}》" for k in missing)
        raise UnprocessableEntityError(f"还需要同意{titles}")
    ip, user_agent = client_context(request)
    await service.record(
        user_id=auth_user.user_id,
        accepted=payload.documents,
        method="dialog",
        entry="reaccept",
        ip=ip,
        user_agent=user_agent,
    )
    # Dependency teardown commits after the response: a failed commit there
    # would already have told the person their acceptance was recorded.
    await session.commit()
    return {"code": 200, "message": "OK", "data": {"pending": []}}
