"""Frontend error intake → 现场 event blocks.

The dogfooding debugger is usually an AGENT, and an agent can never read a
user's browser console — so frontend errors must land somewhere the platform
can query. They become ``kind=event`` blocks (``meta.event_type =
"frontend_error"``) on the topic that was open (or the project's root topic):
the same 现场 timeline everything else already lives in.

Guard rails live here, not in the route: a render-loop error can fire hundreds
of times a second and blocks are forever, so intake is deduped (same
fingerprint within a window collapses to one block) and rate-limited per
project. In-memory state matches the platform's single-process reality (same
assumption as the per-topic chat locks).
"""

import hashlib
import time
import uuid

from pydantic import BaseModel, Field

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
    return f"🐞 前端报错{where}：{err.message}"


def event_meta(err: FrontendErrorIn) -> dict:
    meta: dict = {"event_type": "frontend_error"}
    if err.stack:
        meta["stack"] = err.stack
    if err.source:
        meta["source"] = err.source
    if err.page:
        meta["page"] = err.page
    return meta
