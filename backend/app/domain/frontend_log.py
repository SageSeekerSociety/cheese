"""Frontend error intake → run records the admin page reads.

Nobody can read a user's browser console, so frontend errors must land
somewhere the platform can query: a run record (``kind = "frontend_error"``)
with the project and the conversation that was open. It belongs to no
conversation — the people in a room are not who reads the platform's own
errors.

Guard rails live here, not in the route: a render-loop error can fire hundreds
of times a second, so intake is deduped (same fingerprint within a window
collapses to one record) and rate-limited per project. In-memory state
matches the platform's single-process reality (same assumption as the
per-topic chat locks).
"""

import hashlib
import time
import uuid

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerting
from app.core.errors import NotFoundError
from app.domain.agent.platform_notices import SEVERITY_ERROR, WHO_HUMAN
from app.domain.conversation.models import Conversation
from app.domain.project.models import Project
from app.domain.run_record.service import keep as keep_record

DEDUP_WINDOW_S = 600.0
MAX_BLOCKS_PER_WINDOW = 30  # per project
_SEEN_PRUNE_AT = 512


class FrontendErrorIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    stack: str | None = Field(default=None, max_length=4000)
    # "file:line" of the throw site, when the browser knew it.
    source: str | None = Field(default=None, max_length=300)
    # Frontend route path at the time of the error.
    page: str | None = Field(default=None, max_length=300)


class FrontendErrorBatchIn(BaseModel):
    project_id: uuid.UUID
    topic_id: uuid.UUID | None = None
    errors: list[FrontendErrorIn] = Field(min_length=1, max_length=10)


def fingerprint(err: FrontendErrorIn) -> str:
    """Identity of an error for dedup: message + first stack line + source.
    The full stack is deliberately excluded — minified column offsets vary
    between reloads of the same broken build."""
    first_stack = (err.stack or "").splitlines()[0] if err.stack else ""
    raw = f"{err.message}\n{first_stack}\n{err.source or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class FrontendErrorIntake:
    """Dedup + rate-limit admission. Injectable clock for tests."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], float] = {}
        self._window: dict[str, list[float]] = {}

    def admit(self, project_id: str, fp: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        cutoff = now - DEDUP_WINDOW_S
        last = self._seen.get((project_id, fp))
        if last is not None and last > cutoff:
            return False
        stamps = [t for t in self._window.get(project_id, []) if t > cutoff]
        if len(stamps) >= MAX_BLOCKS_PER_WINDOW:
            self._window[project_id] = stamps
            return False
        stamps.append(now)
        self._window[project_id] = stamps
        if len(self._seen) >= _SEEN_PRUNE_AT:
            self._seen = {k: t for k, t in self._seen.items() if t > cutoff}
        self._seen[(project_id, fp)] = now
        return True


intake = FrontendErrorIntake()


def event_content(err: FrontendErrorIn) -> str:
    where = f"（{err.page}）" if err.page else ""
    return f"前端报错{where}：{err.message}"


def event_meta(err: FrontendErrorIn) -> dict:
    meta: dict = {
        "event_type": "frontend_error",
        "severity": SEVERITY_ERROR,
        "who": WHO_HUMAN,
    }
    if err.stack:
        meta["stack"] = err.stack
    if err.source:
        meta["source"] = err.source
    if err.page:
        meta["page"] = err.page
    return meta


async def record(db: AsyncSession, body: FrontendErrorBatchIn) -> dict:
    """Admit the batch and keep the survivors. A dropped duplicate still counts
    as received: the reporting client never retries."""
    conversation = await db.get(Conversation, body.topic_id) if body.topic_id else None
    if conversation is not None and conversation.project_id != body.project_id:
        conversation = None
    project = await db.get(Project, body.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    accepted = 0
    for err in body.errors:
        if not intake.admit(str(body.project_id), fingerprint(err)):
            continue
        meta = event_meta(err)
        if conversation is not None:
            meta["conversation"] = str(conversation.id)
        await keep_record(
            db,
            project_id=project.id,
            conversation_id=None,
            content=event_content(err),
            meta=meta,
        )
        accepted += 1
        # Only here: `admit` returned True, so this fingerprint is one nobody has
        # seen in the dedup window. Every repeat of it is already excluded above,
        # which is what keeps a render loop from becoming a thousand alerts.
        alerting.send(
            f"前端报错：{err.message}",
            [
                f"页面：{err.page or '未知'}",
                f"位置：{err.source or '未知'}",
                f"项目：{project.id}",
            ],
        )
    return {"accepted": accepted, "dropped": len(body.errors) - accepted}
