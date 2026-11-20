"""A document's read-only share link (#3774).

Sharing makes one link; whoever holds it reads the document's current version
without signing in — its text and its mini-apps, not its comments, its pending
suggestions or its history. Taking the link back deletes it at once, and sharing
again makes a new one; the document's own address never changes. The token is
the whole secret: the document's address names its project and number, which
can be guessed.

Who shares: for a project's document, whoever's role shares documents
(``share_documents``); for a team's own or a person's own document, the person
it is theirs or an admin of its team. Either way the person must be able to
read it.
"""

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.app_shell_host import shell_url
from app.api.auth import ActorResolverDep
from app.api.doc_access import Reached, may_move_or_delete, reach
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.auth.project_roles import may
from app.core.errors import ForbiddenError, NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.identity.services import IdentityService
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.library.shelf import Shelf
from app.domain.living_doc.services import Documents
from app.domain.living_doc.shares import Shares
from app.domain.project.room_files import (
    ARTIFACT_MIME,
    artifact_kind_for,
    clean_artifact_path,
)
from app.domain.project_permissions import Permission
from app.domain.review.queries import has_accepted
from app.domain.usage import referrals
from app.domain.user.services import user_by_handle
from app.domain.web_snippet.services import Snippets

documents = APIRouter(prefix="/documents", tags=["document-shares"])
shared = APIRouter(prefix="/shared", tags=["document-shares"])

#: Search engines do not list what a link shows: it was given to someone.
_UNLISTED = {"X-Robots-Tag": "noindex, nofollow"}

#: The kinds a shared document draws inline; the same as a document's own.
_IMAGE_KINDS = ("png", "jpg", "gif", "webp")


class ShareLinkOut(BaseModel):
    token: str
    created_by: str
    created_at: datetime


class ShareOut(BaseModel):
    #: The document's link, when it has one.
    link: ShareLinkOut | None
    #: Whether the caller may make or take back the link.
    may_share: bool


async def _may_share(db, reached: Reached) -> bool:
    actor = reached.actor
    if actor.via == "cheese" or not actor.authenticated:
        return False
    doc = reached.doc
    if doc.project_id is not None:
        return await may(db, doc.project_id, actor.handle, Permission.share_documents)
    return await may_move_or_delete(db, reached)


def _link(row) -> dict | None:
    if row is None:
        return None
    return {
        "token": row.token,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


@documents.get("/{document_id}/share", **typed_response(ShareOut))
async def get_share(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The document's link, and whether the caller may change it."""
    reached = await reach(db, resolver, document_id, enforce=True)
    allowed = await _may_share(db, reached)
    row = await Shares(db).of(document_id) if allowed else None
    return ok({"link": _link(row), "may_share": allowed})


async def _sharer(db, resolver, document_id: uuid.UUID) -> Reached:
    reached = await reach(db, resolver, document_id, enforce=True)
    if not await _may_share(db, reached):
        raise ForbiddenError(say("docShareNotAllowed"))
    return reached


@documents.put(
    "/{document_id}/share",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ShareOut),
)
async def share_document(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Make the document's link; the one it has, when it has one."""
    reached = await _sharer(db, resolver, document_id)
    row = await Shares(db).make(document_id, reached.actor.handle)
    await db.commit()
    return ok({"link": _link(row), "may_share": True})


@documents.delete(
    "/{document_id}/share",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ShareOut),
)
async def unshare_document(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Take the link back: it stops working at once."""
    await _sharer(db, resolver, document_id)
    await Shares(db).take_back(document_id)
    await db.commit()
    return ok({"link": None, "may_share": True})


async def _shared(db, token: str):
    doc = await Shares(db).document(token)
    if doc is None:
        raise NotFoundError(say("docShareGone"))
    return doc


class SharedByOut(BaseModel):
    """Who shared the page, for its colophon. ``code`` is their own invite
    code while it still admits someone, so a reader can come in on it."""

    handle: str
    nickname: str | None
    code: str | None
    seats_left: int


class SharedDocumentOut(BaseModel):
    title: str | None
    #: The current version's Markdown: pending suggestions are not in it.
    content: str
    updated_at: datetime
    #: None when the link was made by an AI teammate rather than a person.
    shared_by: SharedByOut | None


@shared.get("/{token}", **typed_response(SharedDocumentOut))
async def read_shared(token: str, db: DbSession, response: Response) -> dict:
    """The shared document's current version, for anyone holding the link."""
    doc = await _shared(db, token)
    shared_by = await _shared_by(db, doc.id)
    await db.commit()
    response.headers.update(_UNLISTED)
    return ok(
        {
            "title": doc.title,
            "content": doc.content,
            "updated_at": doc.updated_at,
            "shared_by": shared_by,
        }
    )


async def _shared_by(db, document_id: uuid.UUID) -> dict | None:
    share = await Shares(db).of(document_id)
    sharer = await user_by_handle(db, share.created_by) if share else None
    if sharer is None or await IdentityService(db).is_agent(sharer.username):
        return None
    return await referrals.guest_pass(
        db, sharer, has_accepted=lambda: has_accepted(db, sharer.username)
    )


async def _shared_app(db, token: str, key: str):
    """A mini-app of the shared document; one that belongs to another
    document is not shared with it."""
    doc = await _shared(db, token)
    snippet = await Snippets(db).require(key)
    if snippet.document_id != doc.id:
        raise ForbiddenError(say("docShareAppElsewhere"))
    return snippet


class SharedAppVersionOut(BaseModel):
    key: str
    version: int
    note: str
    author: str
    checked: bool
    created_at: str


class SharedAppOut(BaseModel):
    key: str
    document_id: str
    document_title: str | None
    versions: list[SharedAppVersionOut]


class SharedAppCodeOut(SharedAppVersionOut):
    code: str
    shell: int
    shell_url: str | None
    heights: dict[str, int]
    shots: list[str]


@shared.get("/{token}/apps/{key}", **typed_response(SharedAppOut))
async def shared_app(key: str, token: str, db: DbSession, response: Response) -> dict:
    """A mini-app of the shared document and its versions."""
    snippet = await _shared_app(db, token, key)
    doc = await Documents(db).get(snippet.document_id)
    response.headers.update(_UNLISTED)
    versions = await Snippets(db).versions(snippet)
    return ok(
        {
            "key": snippet.key,
            "document_id": str(snippet.document_id),
            "document_title": doc.title if doc else None,
            "versions": [Snippets.describe(snippet, v) for v in versions],
        }
    )


@shared.get(
    "/{token}/apps/{key}/versions/{version}",
    **typed_response(SharedAppCodeOut),
)
async def shared_app_version(
    key: str, version: int, token: str, db: DbSession, response: Response
) -> dict:
    """One version's code and the shell it runs in."""
    snippet = await _shared_app(db, token, key)
    found = await Snippets(db).require_version(snippet, version)
    response.headers.update(_UNLISTED)
    return ok(
        {
            **Snippets.describe(snippet, found),
            "code": found.code,
            "shell": found.shell,
            "shell_url": shell_url(found.shell),
            "heights": found.heights,
            "shots": sorted(found.shots),
        }
    )


# `![说明](path)`: how a document names a picture, the same pattern the shared
# page uses to turn those paths into this route's address (SharedDocument.vue).
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def _images_named(markdown: str) -> set[str]:
    """The library paths a document's Markdown shows as pictures."""
    named: set[str] = set()
    for src in _IMAGE_REF.findall(markdown):
        if re.match(r"^(?:[a-z]+:|/)", src, re.IGNORECASE):
            continue  # an address, not a path in the library
        try:
            named.add(clean_artifact_path(src.removeprefix("./")))
        except Exception:  # a path the library could never hold
            continue
    return named


@shared.get(
    "/{token}/image",
    response_class=Response,
    responses={
        200: {
            "description": "The image's bytes.",
            "content": {ARTIFACT_MIME[kind]: {} for kind in _IMAGE_KINDS},
        }
    },
)
async def shared_image(
    token: str, db: DbSession, path: str = Query(max_length=1024)
) -> Response:
    """An image the shared document shows, from its project's library. A
    picture kept in a conversation is not shared with the document, and
    neither is any other picture of the library: the link reaches what the
    document itself names (`![…](path)`), not the shelf it is on."""
    doc = await _shared(db, token)
    clean = clean_artifact_path(path)
    kind = artifact_kind_for(clean)
    name = library.library_name(clean)
    if (
        kind not in _IMAGE_KINDS
        or doc.project_id is None
        or name is None
        or clean not in _images_named(doc.content or "")
    ):
        raise UnprocessableEntityError(say("docShareImageUnavailable"))
    # A share link has no reader to hold to a task's rule, so `题目/` (the
    # task's materials, `mount`) is not part of what a shared document shows:
    # an image from there is refused like any file the link may not reach.
    shelf = Shelf.of_project(doc.project_id, with_task=False)
    data = await library_records.read(db, shelf, name)
    return Response(
        content=data,
        media_type=ARTIFACT_MIME[kind],
        headers={
            **_UNLISTED,
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-cache",
        },
    )
