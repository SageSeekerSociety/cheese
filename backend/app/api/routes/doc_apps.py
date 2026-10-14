"""A document's mini-apps (`:::app <key>@<version>`): their code, versions and
pictures, and how 芝士 keeps a new version.

A mini-app belongs to the document it was made for, and whoever may read that
document may run it — wherever it shows: in that document, copied into another
one, or in a chat. So every read here is checked against the owning document
(``app.api.doc_access.reach``), never against where the block was found.

Only 芝士 writes code. People can look at it, copy it, go back to an older
version (a change to the document's `@<version>`, made in the document) and
ask 芝士 to change it.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.app_shell_host import shell_url
from app.api.auth import ActorResolver, ActorResolverDep
from app.api.doc_access import Reached, reach, require_writable
from app.api.request_deps import DbSession
from app.api.response import Envelope, ok
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError, NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.web_snippet import check
from app.domain.web_snippet.services import SHELL, Snippets, shot_name

# Who writes is decided in the handler: 芝士 only (`_writer`).
router = APIRouter(tags=["documents"], dependencies=[ROUTE_DECIDES])

#: Larger than this is not a snippet any more: a page that size belongs in the
#: project as a website.
MAX_CODE = 200_000


class AppVersionOut(BaseModel):
    """A version as the version menu and the tools show it."""

    key: str
    version: int
    note: str
    #: Who wrote it: always an AI teammate.
    author: str
    #: The person whose request it answered, when there was one.
    requested_by: str | None
    #: Whether the platform's browser ran it before it was kept.
    checked: bool
    created_at: str


class AppOut(BaseModel):
    key: str
    document_id: str
    document_title: str | None
    versions: list[AppVersionOut]


class AppVersionCodeOut(AppVersionOut):
    code: str
    shell: int
    #: Where the shell is served; None when this deployment runs no mini-apps.
    shell_url: str | None
    #: Height in pixels by width, as the platform's browser measured it.
    heights: dict[str, int]
    #: The pictures kept of it, by "<width>-<theme>".
    shots: list[str]


class AppCodeIn(BaseModel):
    code: str = Field(min_length=1)
    #: What this version changed, in a sentence.
    note: str = Field(default="", max_length=200)


async def _snippet(
    db: DbSession, resolver: ActorResolver, key: str, *, enforce: bool = False
):
    """The app, once the caller may reach the document it belongs to."""
    snippet = await Snippets(db).require(key)
    return await reach(db, resolver, snippet.document_id, enforce=enforce), snippet


def _writer(reached: Reached, resolver: ActorResolver) -> tuple[str, str | None]:
    """Who the new version is by, and who asked for it: 芝士, and only 芝士 —
    a person changes an app by asking it."""
    delegation = resolver.delegation()
    if delegation is not None and delegation.agent is not None:
        return delegation.agent, reached.actor.handle
    if reached.actor.via == "cheese":
        return reached.actor.handle, None
    raise ForbiddenError(say("appAgentOnly"))


async def _ran(db: DbSession, body: AppCodeIn) -> check.Ran | None:
    """What the platform's browser saw of the code, before anything is kept.
    Nothing of the request stays open while it runs: that can take a while."""
    if len(body.code) > MAX_CODE:
        raise UnprocessableEntityError(say("appCodeTooLarge", limit=MAX_CODE // 1000))
    await db.commit()
    try:
        return await check.run(body.code, SHELL)
    except check.Refused as refused:
        raise UnprocessableEntityError(
            say("appRunFailed", why=refused.args[0])
        ) from None


@router.get(
    "/apps/{key}",
    response_model=Envelope[AppOut],
    response_model_exclude_unset=True,
)
async def get_app(key: str, db: DbSession, resolver: ActorResolverDep) -> dict:
    """The app's document and every version it has, oldest first."""
    reached, snippet = await _snippet(db, resolver, key)
    versions = await Snippets(db).versions(snippet)
    return ok(
        {
            "key": snippet.key,
            "document_id": str(snippet.document_id),
            "document_title": reached.doc.title,
            "versions": [Snippets.describe(snippet, v) for v in versions],
        }
    )


@router.get(
    "/apps/{key}/versions/{version}",
    response_model=Envelope[AppVersionCodeOut],
    response_model_exclude_unset=True,
)
async def get_app_version(
    key: str, version: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """One version's code, the shell it runs in and where that shell is
    served, and the room it took when the platform ran it."""
    _, snippet = await _snippet(db, resolver, key)
    found = await Snippets(db).require_version(snippet, version)
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


@router.get(
    "/apps/{key}/versions/{version}/shot",
    response_class=Response,
    responses={200: {"description": "The picture.", "content": {"image/png": {}}}},
)
async def get_app_shot(
    key: str,
    version: int,
    db: DbSession,
    resolver: ActorResolverDep,
    width: int = Query(ge=200, le=2000),
    theme: Literal["light", "dark"] = "light",
) -> Response:
    """What the version looked like when the platform ran it, at ``width``."""
    _, snippet = await _snippet(db, resolver, key)
    found = await Snippets(db).require_version(snippet, version)
    data = Snippets.shot(found, shot_name(width, theme))
    if data is None:
        raise NotFoundError(say("appShotMissing"))
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            # A version never changes, nor do its pictures.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )


@router.post(
    "/documents/{document_id}/apps",
    response_model=Envelope[AppVersionOut],
    response_model_exclude_unset=True,
)
async def create_app(
    document_id: uuid.UUID, body: AppCodeIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A new app of the document, at version 1. The document does not show it
    until a block names it: where it goes is the writer's to decide."""
    reached = await reach(db, resolver, document_id, enforce=True)
    await require_writable(db, reached)
    author, requested_by = _writer(reached, resolver)
    ran = await _ran(db, body)
    snippets = Snippets(db)
    snippet = await snippets.create(reached.doc.id, author)
    version = await snippets.add_version(
        snippet,
        project_id=reached.doc.project_id,
        code=body.code,
        note=body.note,
        author=author,
        requested_by=requested_by,
        ran=ran,
    )
    await db.commit()
    return ok(Snippets.describe(snippet, version))


@router.post(
    "/apps/{key}/versions",
    response_model=Envelope[AppVersionOut],
    response_model_exclude_unset=True,
)
async def add_app_version(
    key: str, body: AppCodeIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The app's next version. The documents that show it keep the version
    they name until their `@<version>` is changed."""
    reached, snippet = await _snippet(db, resolver, key, enforce=True)
    await require_writable(db, reached)
    author, requested_by = _writer(reached, resolver)
    project_id = reached.doc.project_id
    ran = await _ran(db, body)
    version = await Snippets(db).add_version(
        snippet,
        project_id=project_id,
        code=body.code,
        note=body.note,
        author=author,
        requested_by=requested_by,
        ran=ran,
    )
    await db.commit()
    return ok(Snippets.describe(snippet, version))
