"""The living document lives in the collaboration service; this is how the
backend talks to it.

Every room's document is a Yjs document held by the collaboration service
(frontend/collab). People edit it there over a WebSocket, with a ticket this
module signs. The service stores the document back here — the Yjs state and
the Markdown it exports — and that store is the only path that records a new
version (see ``store.py``). So a write that does not come from an editor (芝士's
``cheese_doc_set``, a restore, an accepted AI proposal) cannot go to the
database either: the live document would overwrite it at its next store. It
goes to the service, which applies it to the live document and stores it.

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

from app.core.config import settings
from app.core.errors import ConflictError, SystemBusyError

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
        raise SystemBusyError("这个部署没有启用文档协同编辑")
    return hashlib.sha256(
        f"{settings.collab_secret}:cheese-collab-{purpose}".encode()
    ).hexdigest()


def document_name(room_id: uuid.UUID) -> str:
    return f"room:{room_id}"


def room_of(name: str) -> uuid.UUID:
    prefix, _, ident = name.partition(":")
    if prefix != "room":
        raise ValueError(f"not a room document: {name}")
    return uuid.UUID(ident)


def sign_ticket(
    *, room_id: uuid.UUID, handle: str, agent: bool, read_only: bool
) -> str:
    return jwt.encode(
        {
            "doc": document_name(room_id),
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
    room_id: uuid.UUID,
    *,
    content: str,
    base: str | None,
    actor: str,
    operation: dict | None = None,
) -> dict:
    """Make the live document read ``content``, as ``actor``.

    ``base`` is the document the writer read: the change applies only while
    the live document still reads exactly that (``None``: the writer saw no
    document). Otherwise the writer would erase whatever was typed since, and
    the refusal is a ConflictError carrying the current version, like the
    whole-document write it replaces.

    ``operation`` rides through to the store so an idempotent write claims and
    completes its receipt in the same transaction as the version it records.
    Returns what the store answered.
    """
    url = (
        settings.collab_internal_url.rstrip("/")
        + f"/internal/documents/{document_name(room_id)}/replace"
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
                },
                headers={"Authorization": f"Bearer {_key('internal')}"},
            )
    except httpx.HTTPError as exc:
        raise SystemBusyError("文档协同服务暂时无法访问，稍后重试") from exc
    body = response.json() if response.content else {}
    if response.status_code == 409:
        if body.get("error") == "operation":
            raise ConflictError(body.get("message") or "文档操作冲突")
        raise ConflictError(
            "实况文档已经被改过了，你手上这份是旧的",
            data={"doc_version": body.get("doc_version", 0)},
        )
    if response.status_code != 200:
        raise SystemBusyError("文档协同服务没有完成这次写入，稍后重试")
    return body["stored"]
