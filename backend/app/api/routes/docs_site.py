"""The docs site's server side: its sign-in, the admin gate for dev/, and 问芝士.

See ``app.domain.docs_site`` for why each exists. Mounted under /api like every
route (``/api/docs/...``), on the platform and on the docs' own host alike; the
pages themselves are static files nginx serves.
"""

import asyncio
import logging
import re
import time
import uuid
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.admin_common import DbSession
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import get_current_session_id
from app.core.config import GATEWAY_MOUNT, settings
from app.core.db import async_session_factory
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    SystemBusyError,
    ValidationError,
    message_key,
)
from app.core.redis import get_redis_client
from app.core.sentences import exception_text, say
from app.domain.admin.services import AdminService
from app.domain.docs_site import access, assistant, library, retrieval, site, tools
from app.domain.docs_site.limits import AskLimits
from app.domain.feature_stats import pricing
from app.domain.topic.services import TopicService
from app.domain.usage.ledger import Ledger, Rates, payer_for_person
from app.domain.user.sessions import SessionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/docs", tags=["docs"])

_limits: AskLimits | None = None
_background: set[asyncio.Task] = set()


def ask_limits() -> AskLimits:
    global _limits
    if _limits is None:
        _limits = AskLimits(get_redis_client)
    return _limits


# ---------- signing in to the docs: through the platform ----------


@router.post("/grant")
async def docs_grant(
    response: Response,
    auth: Annotated[AuthUserInfo, Depends(require_auth_user)],
    sid: Annotated[uuid.UUID | None, Depends(get_current_session_id)],
) -> dict:
    """On the platform: a 30-second, one-use grant for the docs host, and where
    to post it. The docs sign-in it becomes lasts no longer than this sign-in."""
    if sid is None:
        raise BadRequestError(say("signInFirst"))
    response.headers["Cache-Control"] = "no-store"
    return ok(
        {
            "url": f"{site.origin()}{GATEWAY_MOUNT}/docs/session",
            "grant": access.mint_grant(auth.user_id, sid),
        }
    )


@router.get("/signin", include_in_schema=False)
async def docs_sign_in(path: str = "/") -> Response:
    """On the docs host: off to the platform's sign-in page, which comes back
    with a grant (``views/DocsSignInView.vue``). The docs pages are the same
    files on every deployment, so where the platform is comes from here."""
    if not _destination(path):
        path = "/"
    return RedirectResponse(
        f"{site.platform_origin()}/docs-signin?{urlencode({'path': path})}",
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


def _destination(path: str) -> bool:
    return (
        path.startswith("/")
        and not path.startswith("//")
        and "\\" not in path
        and not any(ord(char) < 32 for char in path)
    )


@router.post("/session", include_in_schema=False)
async def open_docs_session(request: Request, db: DbSession) -> Response:
    """On the docs host: spend a grant, set the docs cookie, go back to the page.

    A plain form post the platform page submits, so the grant stays out of
    URLs, history and referrers. It must come from the platform's own origin:
    the two hosts are same-site, and nothing else may sign a reader in."""
    if not site.on_docs_host(request.headers.get("host")):
        return Response(status_code=404)
    if request.headers.get("origin") != site.platform_origin():
        return Response(status_code=403)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            return Response(status_code=413)
    values = parse_qs(body.decode("utf-8", errors="replace"))
    destination = values.get("path", ["/"])[0] or "/"
    if not _destination(destination):
        return Response(status_code=400)
    try:
        spent = await access.spend_grant(values.get("grant", [""])[0], get_redis_client)
    except access.GrantStoreUnavailable:
        return Response(status_code=503, headers={"Retry-After": "10"})
    if spent is None:
        return Response(status_code=401)
    user_id, sid = spent
    if await SessionService(db).live_handle(user_id, sid) is None:
        return Response(status_code=401)
    response = RedirectResponse(destination, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        access.cookie_name(),
        access.mint_session(user_id, sid),
        max_age=settings.docs_session_seconds,
        path="/",
        httponly=True,
        secure=access.cookie_secure(),
        # Lax, not Strict: a developer page opened from a link elsewhere is a
        # cross-site navigation, and Strict would show it the sign-in gate.
        # Lax still keeps the cookie off cross-site POSTs; same-site ones are
        # stopped by the Origin check in `docs_reader`.
        samesite="lax",
    )
    return response


async def docs_reader(request: Request, db: DbSession) -> access.Reader:
    """The reader a docs-host request comes from, by the docs' own cookie.

    A request that changes something must also carry the docs' own Origin: the
    docs and the platform are same-site, so SameSite alone lets a page on any
    other okcheese.com host send the cookie along."""
    if request.method not in ("GET", "HEAD") and (
        request.headers.get("origin") != site.origin()
    ):
        raise ForbiddenError("Not from the docs site")
    who = await access.reader(
        db, request.cookies.get(access.cookie_name()), request.headers.get("host")
    )
    if who is None:
        raise AuthenticationRequiredError("Login required")
    return who


# ---------- dev/: platform admins only ----------


@router.get("/dev-access/check", include_in_schema=False)
async def check_dev_access(request: Request, db: DbSession) -> Response:
    """nginx's ``auth_request`` for every file under dev/: 204 lets it through.

    Whether the holder is still signed in, and still an admin, is asked again
    on every file, so the door closes without waiting for the cookie to expire."""
    token = request.cookies.get(access.cookie_name())
    if access.is_internal(token):
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    who = await access.reader(db, token, request.headers.get("host"))
    if who is None:
        return Response(status_code=401, headers={"Cache-Control": "no-store"})
    if not await access.admins.contains(who.handle, AdminService(db).admin_handles):
        return Response(status_code=403, headers={"Cache-Control": "no-store"})
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


# ---------- 问芝士 ----------

_PAGE = re.compile(r"^[a-z0-9-]{1,64}$")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=1200)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    # The public page the reader is on, by slug; developer pages are not answered from.
    page: str | None = None
    history: list[Turn] = Field(default_factory=list, max_length=6)
    # Text the reader selected on the page and asked about (划词问芝士).
    quote: str | None = Field(default=None, max_length=600)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty question")
        return v

    @field_validator("page")
    @classmethod
    def _page(cls, v: str | None) -> str | None:
        return v if v and _PAGE.match(v) else None


def _refuse(status: int, message: str, retry_after: int = 0) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    body: dict = {"code": status, "message": message}
    key = message_key(message)
    if key is not None:
        # A catalog sentence: the browser renders it in its reader's language.
        body["error"] = {"message": message, "i18n": key}
    return JSONResponse(body, status_code=status, headers=headers)


@router.post("/ask")
async def ask(
    body: AskRequest,
    db: DbSession,
    auth: Annotated[access.Reader, Depends(docs_reader)],
) -> Response:
    """Answer one question from the public docs, streamed as server-sent events:
    ``sources`` (what the answer may cite), ``tool`` (what it is looking at),
    ``delta`` (text), ``error``, ``done``.

    Which way the answer is found is ``settings.docs_assistant_agentic``: the
    model searches and reads the docs itself (``assistant.run_agent``), or one
    round of retrieval feeds it the sections (the original path, kept)."""
    started = time.monotonic()
    limits = ask_limits()
    verdict = await limits.admit(auth.user_id)
    if not verdict.allowed:
        return _refuse(429, verdict.message, verdict.retry_after)

    index = await retrieval.source.get()
    if index is None:
        await limits.release(auth.user_id)
        return _refuse(503, say("askCheeseDocsUnreadable"), 30)

    # The question is the asker's, so its cost comes out of their credits.
    rates = Rates.of(settings.docs_assistant_model, await pricing.model_rates())
    if rates is None:
        await limits.release(auth.user_id)
        return _refuse(503, say("askCheeseNotOpen"), 60)
    refused = await Ledger(db).admit(await payer_for_person(db, auth.user_id))
    await db.commit()
    if refused is not None:
        await limits.release(auth.user_id)
        return _refuse(429, refused.message, refused.retry_after_s())

    if settings.docs_assistant_agentic:
        result = assistant.Outcome()
        docs = tools.Docs(index)
        key = await assistant.gateway_key(db)
        if key is None:
            await limits.release(auth.user_id)
            return _refuse(503, say("askCheeseNotOpen"), 60)
        if not limits.try_slot():
            await limits.release(auth.user_id)
            return _refuse(503, say("askCheeseBusy"), 10)

        async def agent_events():
            try:
                async for chunk in assistant.run_agent(
                    key,
                    body.question,
                    docs,
                    result,
                    history=[t.model_dump() for t in body.history],
                    quote=body.quote,
                ):
                    yield chunk
                yield assistant.sse("done", {})
            finally:
                limits.free_slot()
                # The reader may have gone; the bookkeeping must not go with them.
                _bookkeep(auth.user_id, body, result, started, rates)

        return _stream(agent_events())

    # A quoted passage says what the question is about; search with both.
    query = f"{body.quote}\n{body.question}" if body.quote else body.question
    hits = retrieval.relevant(
        index.search(query, page_url=site.page_path(body.page) if body.page else None)
    )
    result = assistant.Outcome(sources=[h.section.url for h in hits])

    key = await assistant.gateway_key(db) if hits else None
    if hits and key is None:
        await limits.release(auth.user_id)
        return _refuse(503, say("askCheeseNotOpen"), 60)
    if hits and not limits.try_slot():
        await limits.release(auth.user_id)
        return _refuse(503, say("askCheeseBusy"), 10)

    async def events():
        try:
            yield assistant.sse("sources", {"sources": assistant.sources_payload(hits)})
            if key is None:  # nothing relevant, so no key was fetched either
                result.outcome = "no_match"
                yield assistant.sse("delta", {"text": assistant.NO_MATCH})
            else:
                messages = assistant.build_messages(
                    body.question,
                    hits,
                    [t.model_dump() for t in body.history],
                    quote=body.quote,
                )
                async for chunk in assistant.stream_answer(key, messages, result):
                    yield chunk
            yield assistant.sse("done", {})
        finally:
            if hits:
                limits.free_slot()
            _bookkeep(auth.user_id, body, result, started, rates)

    return _stream(events())


def _stream(events) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        # nginx must hand each event on as it comes rather than buffer the answer.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _bookkeep(
    user_id: int,
    body: AskRequest,
    result: assistant.Outcome,
    started: float,
    rates: Rates,
) -> None:
    """Record and charge the question without holding up the response: the
    reader may have gone, and the bookkeeping must not go with them."""
    task = asyncio.get_running_loop().create_task(
        _settle(user_id, body, result, started, rates)
    )
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _settle(
    user_id: int,
    body: AskRequest,
    result: assistant.Outcome,
    started: float,
    rates: Rates,
) -> None:
    await ask_limits().release(user_id)
    try:
        async with async_session_factory() as session:
            assistant.record(
                session,
                user_id=user_id,
                question=body.question,
                page=body.page,
                result=result,
                started=started,
            )
            # Every round the answer took is one charge; a question that never
            # reached the model costs nothing.
            if result.prompt_tokens is not None:
                await Ledger(session).charge_priced(
                    await payer_for_person(session, user_id),
                    user_id=user_id,
                    model=settings.docs_assistant_model,
                    rates=rates,
                    input_tokens=result.prompt_tokens,
                    output_tokens=result.completion_tokens or 0,
                    cache_read_tokens=result.cache_read_tokens,
                    cache_write_tokens=result.cache_write_tokens,
                    kind="docs_ask",
                )
            await session.commit()
    except Exception:  # noqa: BLE001 — the reader has their answer either way
        logger.warning("settling a docs question failed", exc_info=True)


# ---------- the docs, for AI teammates: cheese_docs_search / cheese_docs_read ----


class AgentDocsIn(BaseModel):
    #: The room asking; none for a person's own 芝士, which reads only the
    #: pages every reader may.
    topic: uuid.UUID | None = None
    query: str | None = Field(default=None, max_length=300)
    page: str | None = Field(default=None, max_length=200)


async def _agent_scope(
    body: AgentDocsIn, db: DbSession, actor: ActorResolverDep
) -> bool:
    """Authorize the caller for the room it names; whether it may read developer
    pages is a property of that room's project, not of the caller. A caller
    naming no room reads only what every reader may."""
    if body.topic is None:
        who = await actor.resolve()
        if not who.authenticated:
            raise AuthenticationRequiredError("Login required")
        return False
    place = await TopicService(db).place_or_404(body.topic)
    who = await actor.resolve(topic_id=place.room_id, project_id=place.project_id)
    await actor.authorize_topic(
        who, project_id=place.project_id, topic_id=place.room_id
    )
    return await library.reads_dev_docs(db, place.project_id)


@router.post("/agent/search", include_in_schema=False)
async def agent_search_docs(
    body: AgentDocsIn, db: DbSession, actor: ActorResolverDep
) -> dict:
    """cheese_docs_search: the best sections of the manual for a query."""
    if not body.query or not body.query.strip():
        raise ValidationError(say("queryRequired"))
    dev = await _agent_scope(body, db, actor)
    found = await library.search(body.query, dev=dev)
    if found is None:
        raise SystemBusyError(say("docsIndexUnavailable"))
    return ok(
        {
            "dev": dev,
            "hits": [
                {
                    "title": f.title,
                    "heading": f.heading,
                    # Absolute, so the agent can hand the link to a person as is.
                    "url": site.public_url(f.url),
                    "excerpt": f.excerpt,
                    "dev": f.dev,
                }
                for f in found
            ],
        }
    )


@router.post("/agent/read", include_in_schema=False)
async def agent_read_docs(
    body: AgentDocsIn, db: DbSession, actor: ActorResolverDep
) -> dict:
    """cheese_docs_read: one page's Markdown, as readers fetch it."""
    if not body.page or not body.page.strip():
        raise ValidationError(say("pageRequired"))
    dev = await _agent_scope(body, db, actor)
    try:
        text = await library.read_page(body.page, dev=dev)
    except library.DevDocsForbidden as exc:
        raise ForbiddenError(say("devDocsOwnProjectsOnly")) from exc
    except ValueError as exc:
        raise ValidationError(exception_text(exc)) from exc
    if text is None:
        raise NotFoundError(say("docsPageNotFound", page=body.page))
    return ok({"page": library.page_slug(body.page), "markdown": text})
