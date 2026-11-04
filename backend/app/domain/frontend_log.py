"""Frontend error intake → run records the admin page reads.

Nobody can read a user's browser console, so frontend errors must land
somewhere the platform can query: a run record (``kind = "frontend_error"``)
with the project and the conversation that was open. It belongs to no
conversation — the people in a room are not who reads the platform's own
errors.

The guard rails against a render loop live here, not in the route: a bad error
can fire hundreds of times a second, so intake is deduped (same fingerprint
within a window collapses to one record) and rate-limited per project.
In-memory state matches the platform's single-process reality (same assumption
as the per-topic chat locks). Whether the project exists is not one of these:
this module is in the bottom layer and reads no project table, so the route
asks the project domain before the batch gets here.
"""

import hashlib
import ipaddress
import re
import time
import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerting
from app.domain.conversation.models import Conversation
from app.domain.notice_meta import SEVERITY_ERROR, WHO_HUMAN
from app.domain.run_record.service import keep as keep_record

DEDUP_WINDOW_S = 600.0
MAX_BLOCKS_PER_WINDOW = 30  # per project
# Alerts per window across every project. Reports come in unauthenticated (a
# page that is going away can only send a beacon, which carries no token), so
# the per-project cap alone lets anyone who knows a few project ids send the
# admins thirty alerts of their own wording each, every ten minutes. Past this
# the reports are still kept; only the alerts stop.
MAX_ALERTS_PER_WINDOW = 20
_SEEN_PRUNE_AT = 512


# A tab still running the previous build asks for a chunk the deploy removed.
# The page already recovers from that by itself — `frontend/src/services/
# staleBuild.ts` reloads it on these same messages (keep the two in step) — and
# what still arrives here is the sibling imports of the route it was loading,
# rejected while the reload was under way: 29 + 5 alerts in two weeks, none of
# which anyone could act on. Kept as run records, not alerted.
_STALE_BUILD_RE = re.compile(
    r"Failed to fetch dynamically imported module|error loading dynamically "
    r"imported module|Importing a module script failed|Unable to preload CSS",
    re.IGNORECASE,
)


def _from_a_dev_server(origin: str | None) -> bool:
    """Whether the page that reported was served by someone's local or preview
    dev server (``http://localhost:5317``, ``http://127.0.0.1:3000``) whose API
    proxy points at this backend. Its errors are that developer's, of code that
    may never ship; they are kept, but not alerted to everyone. Not a desktop
    client: Tauri's origins (``tauri://localhost``, ``http://tauri.localhost``)
    are a user's app and still alert."""
    if not origin:
        return False
    try:
        parts = urlsplit(origin.strip().lower())
        host = parts.hostname or ""
    except ValueError:  # an Origin no browser sends ("http://[::1"): not a dev server
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def worth_alerting(err: "FrontendErrorIn", origin: str | None) -> bool:
    return not (_from_a_dev_server(origin) or _STALE_BUILD_RE.search(err.message))


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
        self._alerts: list[float] = []

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

    def may_alert(self, now: float | None = None) -> bool:
        """Spend one of the window's alerts, across all projects."""
        now = time.time() if now is None else now
        cutoff = now - DEDUP_WINDOW_S
        self._alerts = [t for t in self._alerts if t > cutoff]
        if len(self._alerts) >= MAX_ALERTS_PER_WINDOW:
            return False
        self._alerts.append(now)
        return True


intake = FrontendErrorIntake()


def event_content(err: FrontendErrorIn) -> str:
    where = f"（{err.page}）" if err.page else ""
    return f"前端报错{where}：{err.message}"


def event_meta(err: FrontendErrorIn) -> dict:
    meta: dict = {
        "event_type": "frontend_error",
        # 管理后台「一种事一行」认的就是这个键（run_record/admin 的 `_key`），和
        # 摄入去重用的是同一个身份。不写它，那张表退回「抹掉数字的那句话」认事，而
        # 这条报错的话里写着出事的页面——路径里带着项目和话题的 uuid，抹数字抹不到。
        # 同一个 bug 于是每个房间各占一行，次数也就说不出它铺开了多远。
        "fingerprint": fingerprint(err),
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


async def record(
    db: AsyncSession, body: FrontendErrorBatchIn, *, origin: str | None = None
) -> dict:
    """Admit the batch and keep the survivors. A dropped duplicate still counts
    as received: the reporting client never retries.

    The caller has already answered whether the project exists — a batch for a
    project that does not exist is refused with a 404 before it reaches here.
    """
    conversation = await db.get(Conversation, body.topic_id) if body.topic_id else None
    if conversation is not None and conversation.project_id != body.project_id:
        conversation = None
    accepted = 0
    for err in body.errors:
        if not intake.admit(str(body.project_id), fingerprint(err)):
            continue
        meta = event_meta(err)
        if conversation is not None:
            meta["conversation"] = str(conversation.id)
        await keep_record(
            db,
            project_id=body.project_id,
            conversation_id=None,
            content=event_content(err),
            meta=meta,
        )
        accepted += 1
        # Only here: `admit` returned True, so this fingerprint is one nobody has
        # seen in the dedup window. Every repeat of it is already excluded above,
        # which is what keeps a render loop from becoming a thousand alerts.
        if not worth_alerting(err, origin) or not intake.may_alert():
            continue
        alerting.send(
            f"前端报错：{err.message}",
            [
                f"页面：{err.page or '未知'}",
                f"位置：{err.source or '未知'}",
                f"项目：{body.project_id}",
            ],
        )
    return {"accepted": accepted, "dropped": len(body.errors) - accepted}
