"""The living document lives in the collaboration service; this is how the
backend talks to it.

Every document is a Yjs document held by the collaboration service
(frontend/collab). People edit it there over a WebSocket, with a ticket this
module signs. The service stores the document back here — the Yjs state and
the Markdown it exports — and that store is the only path that records a new
version (see ``store.py``). So a write that does not come from an editor (芝士's
``cheese_doc_set`` and ``cheese_doc_edit``, a restore) cannot go to the
database either: the live document would overwrite it at its next store. It
goes to the service, which applies it to the live document and stores it:
``replace`` for the whole document, ``edit`` for passages of it.

One shared secret, three keys derived from it for three jobs: tickets the
browser carries, the bearer the two services show each other, and nothing
else. A ticket cannot double as the bearer.
"""

import hashlib
import hmac
import time
import uuid

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, SystemBusyError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.living_doc.models import Document
from app.domain.living_doc.repositories import DocumentRepository

#: How long a ticket opens a connection for. The connection outlives it; a
#: reconnect asks for a new one.
TICKET_TTL_S = 120

#: A replace waits for the service to store the result here, which is a
#: database transaction away.
_REPLACE_TIMEOUT_S = 30

#: How requests reach the service. None is the network; a test puts a stand-in
#: for the service here (tests/support/collab.py).
transport: httpx.AsyncBaseTransport | None = None


class CollabRefused(Exception):
    """A request that claims to come from the collaboration service could not
    prove it."""


def enabled() -> bool:
    return bool(settings.collab_secret)


def _key(purpose: str) -> str:
    if not settings.collab_secret:
        raise SystemBusyError(say("collabDisabled"))
    return hashlib.sha256(
        f"{settings.collab_secret}:cheese-collab-{purpose}".encode()
    ).hexdigest()


def document_name(document_id: uuid.UUID) -> str:
    return f"doc:{document_id}"


def document_of(name: str) -> uuid.UUID:
    prefix, _, ident = name.partition(":")
    if prefix != "doc":
        raise ValueError(f"not a document: {name}")
    return uuid.UUID(ident)


async def document_named(session: AsyncSession, name: str) -> Document | None:
    """The document a service-side name (``doc:<id>``) names, if it exists."""
    try:
        document_id = document_of(name)
    except ValueError:
        return None
    return await DocumentRepository(session).get(document_id)


def sign_ticket(
    *, document_id: uuid.UUID, handle: str, agent: bool, read_only: bool
) -> str:
    return jwt.encode(
        {
            "doc": document_name(document_id),
            "sub": handle,
            "agent": agent,
            "ro": read_only,
            "exp": int(time.time()) + TICKET_TTL_S,
        },
        _key("ticket"),
        algorithm="HS256",
    )


def verify_service(authorization: str | None) -> None:
    """Refuse anything but the collaboration service itself."""
    expected = f"Bearer {_key('internal')}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise CollabRefused("not the collaboration service")


async def replace(
    document_id: uuid.UUID,
    *,
    content: str,
    base: str | None,
    actor: str,
    operation: dict | None = None,
    check: bool = False,
) -> dict:
    """Make the live document read ``content``, as ``actor``.

    ``base`` is the document the writer read: the change applies only while
    the live document still reads exactly that (``None``: the writer saw no
    document). Otherwise the writer would erase whatever was typed since, and
    the refusal is a ConflictError carrying the current version, like the
    whole-document write it replaces.

    ``operation`` rides through to the store so an idempotent write claims and
    completes its receipt in the same transaction as the version it records.
    ``check`` is for a write in Markdown from outside an editor: the service
    refuses it, unapplied, when converting it would lose visible text, and says
    where and how to fix it (UnprocessableEntityError, ``data.line``).
    Returns what the store answered.
    """
    url = (
        settings.collab_internal_url.rstrip("/")
        + f"/internal/documents/{document_name(document_id)}/replace"
    )
    try:
        async with httpx.AsyncClient(
            timeout=_REPLACE_TIMEOUT_S, transport=transport
        ) as client:
            response = await client.post(
                url,
                json={
                    "content": content,
                    "base": base,
                    "actor": actor,
                    "operation": operation,
                    "check": check,
                },
                headers={"Authorization": f"Bearer {_key('internal')}"},
            )
    except httpx.HTTPError as exc:
        raise SystemBusyError(say("collabUnreachable")) from exc
    body = response.json() if response.content else {}
    if response.status_code == 422 and body.get("error") == "content":
        raise UnprocessableEntityError(body["message"], data={"line": body.get("line")})
    if response.status_code == 409:
        if body.get("error") == "operation":
            raise ConflictError(body.get("message") or say("docOperationConflict"))
        raise ConflictError(
            say("liveDocStale"),
            data={"doc_version": body.get("doc_version", 0)},
        )
    if response.status_code != 200:
        raise SystemBusyError(say("collabWriteIncomplete"))
    return body["stored"]


#: Why the service refused an edit: the sentence its writer acts on, said with
#: ``n``, the edit's place in the request counted from 1.
_EDIT_REFUSALS = {
    "empty": "docEditNoOriginal",
    "not_found": "docEditOriginalNotFound",
    "ambiguous": "docEditOriginalAmbiguous",
    "structure": "docEditChangesStructure",
    "unstable": "docEditParagraphUnstable",
    "suggested": "docEditHitsSuggestion",
}


async def edit(
    document_id: uuid.UUID,
    *,
    edits: list[dict],
    actor: str,
    requested_by: str | None = None,
    mode: str = "direct",
    reason: str | None = None,
) -> dict:
    """Change passages of the live document, as ``actor``.

    Each edit replaces ``old`` — text that occurs exactly once in the
    document's Markdown — with ``new``, in order. ``mode`` "suggest" leaves
    the text as it is and proposes each change as a suggestion someone has to
    accept. Nothing is applied unless every edit can be: a refusal says which
    edit and why (UnprocessableEntityError for one the writer has to restate,
    ConflictError for one blocked by the document's state), ``data.index``
    counting from 0. ``requested_by`` is the person the change was made for.
    Returns ``{"stored": <the store's answer>, "edits": [...]}``, each edit
    carrying its ``suggestion_id`` in suggest mode.
    """
    url = (
        settings.collab_internal_url.rstrip("/")
        + f"/internal/documents/{document_name(document_id)}/edit"
    )
    try:
        async with httpx.AsyncClient(
            timeout=_REPLACE_TIMEOUT_S, transport=transport
        ) as client:
            response = await client.post(
                url,
                json={
                    "edits": edits,
                    "actor": actor,
                    "requested_by": requested_by,
                    "mode": mode,
                    "reason": reason,
                },
                headers={"Authorization": f"Bearer {_key('internal')}"},
            )
    except httpx.HTTPError as exc:
        raise SystemBusyError(say("collabUnreachable")) from exc
    body = response.json() if response.content else {}
    if response.status_code in (409, 422) and "index" in body:
        index = int(body["index"])
        data = {"index": index, "reason": body.get("reason") or body.get("error")}
        if body.get("error") == "content":
            message = say(
                "docEditContentRefused", n=index + 1, reason=body.get("message", "")
            )
            data["line"] = body.get("line")
        else:
            key = _EDIT_REFUSALS.get(str(body.get("reason")), "docEditCannotApply")
            message = say(key, n=index + 1)
        if response.status_code == 409:
            raise ConflictError(message, data=data)
        raise UnprocessableEntityError(message, data=data)
    if response.status_code != 200:
        raise SystemBusyError(say("collabEditIncomplete"))
    return body
