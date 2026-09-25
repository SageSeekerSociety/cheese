"""The docs site's server side: the admin pass for /docs/dev/, and 问芝士.

See ``app.domain.docs_site`` for why each exists. Mounted under /api like every
route (``/api/docs/...``); the pages themselves are static files nginx serves.
"""

import asyncio
import logging
import re
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.redis import get_redis_client
from app.domain.admin.services import AdminService
from app.domain.docs_site import access, assistant, retrieval
from app.domain.docs_site.limits import AskLimits

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/docs", tags=["docs"])

_limits: AskLimits | None = None
_background: set[asyncio.Task] = set()


def ask_limits() -> AskLimits:
    global _limits
    if _limits is None:
        _limits = AskLimits(get_redis_client)
    return _limits


# ---------- /docs/dev/: platform admins only ----------


@router.post("/dev-access", status_code=204)
async def grant_dev_access(handle: PlatformAdminDep) -> Response:
    """Trade the caller's sign-in for a pass to /docs/dev/ (admins only)."""
    token, ttl = access.issue(handle)
    response = Response(status_code=204)
    response.set_cookie(
        access.COOKIE,
        token,
        max_age=ttl,
        path=access.COOKIE_PATH,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="strict",
    )
    return response


@router.get("/dev-access/check", include_in_schema=False)
async def check_dev_access(request: Request, db: DbSession) -> Response:
    """nginx's ``auth_request`` for every file under /docs/dev/: 204 lets it through."""
    handle = access.holder(request.cookies.get(access.COOKIE))
    if handle is None:
        return Response(status_code=401)
    if not await access.admins.contains(handle, AdminService(db).admin_handles):
        return Response(status_code=403)
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
    return JSONResponse(
        {"code": status, "message": message}, status_code=status, headers=headers
    )


@router.post("/ask")
async def ask(
    body: AskRequest,
    db: DbSession,
    auth: Annotated[AuthUserInfo, Depends(require_auth_user)],
) -> Response:
    """Answer one question from the public docs, streamed as server-sent events:
    ``sources`` (what the answer may cite), ``delta`` (text), ``error``, ``done``."""
    started = time.monotonic()
    limits = ask_limits()
    verdict = await limits.admit(auth.user_id)
    if not verdict.allowed:
        return _refuse(429, verdict.message, verdict.retry_after)

    index = await retrieval.source.get()
    if index is None:
        await limits.release(auth.user_id)
        return _refuse(503, "问芝士暂时读不到文档，稍后再试。", 30)
    hits = retrieval.relevant(
        index.search(
            body.question, page_url=f"/docs/{body.page}" if body.page else None
        )
    )
    result = assistant.Outcome(sources=[h.section.url for h in hits])

    key = await assistant.gateway_key(db) if hits else None
    if hits and key is None:
        await limits.release(auth.user_id)
        return _refuse(503, "问芝士暂未开放，稍后再试。", 60)
    if hits and not limits.try_slot():
        await limits.release(auth.user_id)
        return _refuse(503, "现在问的人有点多，稍后再试。", 10)

    async def events():
        try:
            yield assistant.sse("sources", {"sources": assistant.sources_payload(hits)})
            if key is None:  # nothing relevant, so no key was fetched either
                result.outcome = "no_match"
                yield assistant.sse("delta", {"text": assistant.NO_MATCH})
            else:
                messages = assistant.build_messages(
                    body.question, hits, [t.model_dump() for t in body.history]
                )
                async for chunk in assistant.stream_answer(key, messages, result):
                    yield chunk
            yield assistant.sse("done", {})
        finally:
            if hits:
                limits.free_slot()
            # The reader may have gone; the bookkeeping must not go with them.
            task = asyncio.get_running_loop().create_task(
                _settle(auth.user_id, body, result, started)
            )
            _background.add(task)
            task.add_done_callback(_background.discard)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # nginx must hand each event on as it comes rather than buffer the answer.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _settle(
    user_id: int, body: AskRequest, result: assistant.Outcome, started: float
) -> None:
    await ask_limits().release(user_id)
    try:
        async with async_session_factory() as session:
            await assistant.record(
                session,
                user_id=user_id,
                question=body.question,
                page=body.page,
                result=result,
                started=started,
            )
    except Exception:  # noqa: BLE001 — a lost analytics row must not surface to anyone
        logger.warning("recording a docs question failed", exc_info=True)
