"""The platform names tasks, and renames them only when their direction changes.

A task a person opens without a title starts as 「新任务」. Naming it is a
platform job, not the AI teammate's: one structured call to a small model
through the gateway, off the agent's turn, at three moments —

* **name**: a still-unnamed task gets its first real message from a person (in
  parallel with the work it starts), or its session goes idle as its runner
  reports it — and for a task
  turned from a message, that message is its first (`naming_material._origin`);
* **calibrate**, once: its session went idle, or three people's messages are in
  — the opening line is rarely the whole story;
* **follow**: something suggests the task changed direction (its document was
  rewritten, work was handed in for acceptance) or many messages went by.
  Throttled, and the model is asked *whether* to change first: a title is how
  people find a task again, so keeping it is the default.

A task an AI teammate proposed arrives with the title it proposed, already
calibrated: only a change of direction renames it. A title a person typed
(``TaskTitleSource.human``) is final. An automatic rename is written only
against the version it was computed from, so someone renaming while a name is
being generated always wins, and it is written quietly: the new title shows
where the old one did, and a person who disagrees renames the task. A project
can turn automatic naming off (``settings.task_naming = "manual"``).

Where the platform cannot name at all (no gateway), an unnamed task's own
session is reminded to name it with `cheese_room(action=rename)` (`agent/room/turn.py`).

Everything here fails quietly: no gateway, no key, a timeout or an unusable
answer means the task keeps its title until the next trigger. Quiet is not the
same as traceless, though — every trigger that asks the model nothing says so
in one line, with a word for why (``_unasked``), because a task that is never
named is otherwise a question with no answer anywhere.

This module is the judgement and the entry points, not the whole job. What the
model reads is built in `naming_material.py` and the call itself, with the
answer and a title that does not fit going back for another try, is in
`naming_model.py`. The split is by size — this module was 922 lines — and
everything those two define is imported back below, so every name that used to
live here is still reachable here, and no call point or monkeypatch target
moved.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import model_roles
from app.core.config import settings
from app.core.live_frames import show_once_committed
from app.core.redis import get_redis_client
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.living_doc.services import Documents
from app.domain.project.models import Project
from app.domain.room_task import heard
from app.domain.room_task.models import (
    Task,
    TaskStatus,
    TaskTitle,
    TaskTitleSource,
)

# 这些名字原来**定义在本模块**，拆分后搬到了 `naming_material.py` /
# `naming_model.py`。这条 import 不是「顺手拿来用」，是把公开路径留在原地：
# 调用点一直按 `naming.<名字>` 取它们，按字符串打的 monkeypatch 也钉在这个模块上，
# 换文件不该让任何一行跟着改。所以 F401 在这里是故意的。
from app.domain.room_task.naming_material import (  # noqa: F401
    _CONVERSATION_CHARS,
    _GOAL_CHARS,
    _MENTION,
    _MESSAGE_CHARS,
    _MESSAGES,
    _STAGE_ASK,
    Line,
    _conversation,
    _escape,
    _material,
    _messages,
    _newest,
    _origin,
    _render,
    _said,
)
from app.domain.room_task.naming_model import (  # noqa: F401
    _ANSWER_TOKENS,
    _PAIRS,
    _PUNCT,
    _RETRIES,
    _RETRY_FLOOR_S,
    _TRAILING,
    SYSTEM_PROMPT,
    USAGE_KIND,
    Answer,
    SessionFactory,
    Verdict,
    _ask,
    _conversation_with_model,
    _fitted,
    _record_usage,
    _too_wide,
    normalize_title,
    parse_verdict,
    same_title,
)
from app.domain.service_keys import KeySpec, service_key

logger = logging.getLogger(__name__)

Reason = Literal["message", "idle", "signal"]
Stage = Literal["name", "calibrate", "follow"]

SETTINGS_KEY = "task_naming"
MODES = ("auto", "manual")

# The opening line has to say something before it is worth naming a task by:
# 「在吗」「@芝士」 wait for the next message or the session going idle. What
# counts is what `_said` has left, and a mention there is only the handle
# (`naming_material._said`), so an `@` inside a sentence is not mistaken for a
# mention and does not read as an empty opener.
_SUBSTANTIVE_CHARS = 5
_CALIBRATE_AFTER_PEOPLE = 3


def naming_mode(project_settings: dict | None) -> str:
    mode = (project_settings or {}).get(SETTINGS_KEY)
    return mode if mode in MODES else "auto"


def _key_spec() -> KeySpec:
    return KeySpec(
        name="topic-naming-gateway-key",
        alias="topic-naming",
        model=model_roles.model("topic_naming_model"),
        budget_usd=settings.topic_naming_budget_usd,
        rpm=300,
    )


def available() -> bool:
    """Whether the platform can name tasks at all (a gateway to call)."""
    return bool(settings.llm_gateway_admin_base and settings.llm_gateway_admin_key)


# ---------- deciding when ----------


def _now() -> datetime:
    return datetime.now(UTC)


def _redis_key(kind: str, task_id: uuid.UUID) -> str:
    return f"task-naming:{kind}:{task_id}"


async def _redis_call(fn: Callable[..., object], *args, **kwargs) -> object:
    """A Valkey call that never fails the caller: naming is best effort."""
    try:
        return await fn(*args, **kwargs)  # type: ignore[misc]
    except Exception:
        logger.info("task naming could not reach valkey", exc_info=True)
        return None


async def _messages_since(
    session: AsyncSession, task_id: uuid.UUID, since: datetime | None
) -> int:
    stmt = select(func.count()).where(
        Block.conversation_id == task_id,
        Block.kind == BlockKind.message,
        Block.author_type == AuthorType.participant,
    )
    if since is not None:
        stmt = stmt.where(Block.created_at > since)
    return int(await session.scalar(stmt) or 0)


def _nameable(task: Task) -> bool:
    """An open task whose title is not a person's."""
    return task.status == TaskStatus.open and task.title_source != TaskTitleSource.human


@dataclass(frozen=True)
class Asked:
    """What a trigger calls for: a stage to judge, or nothing and a short word
    for why. ``run`` logs that word — a task that keeps its title is otherwise
    silent, and 「为什么这个任务没改名」 then has no answer anywhere."""

    stage: Stage | None = None
    why: str = ""


async def _stage(session: AsyncSession, task: Task, reason: Reason) -> Asked:
    """Which judgement, if any, this trigger calls for."""
    if task.title_source == TaskTitleSource.placeholder:
        lines = await _conversation(session, task)
        people = [line for line in lines if line.person and line.attachment is None]
        has_attachment = any(
            line.person and line.attachment is not None for line in lines
        )
        if not people and not has_attachment:
            return Asked(why="no_person_in_the_task_yet")
        if has_attachment:
            # Naming cannot read the attachment. Wait for an explanation rather
            # than making the opener's length stand in for knowing its subject.
            last_attachment = max(
                index
                for index, line in enumerate(lines)
                if line.person and line.attachment is not None
            )
            explained = any(
                line.attachment is None and len(_said(line.text)) >= _SUBSTANTIVE_CHARS
                for line in lines[last_attachment + 1 :]
            )
            if not explained and task.document_id is not None:
                doc = await Documents(session).get(task.document_id)
                explained = bool(doc and (doc.content or "").strip())
            if not explained:
                return Asked(why="awaiting_attachment_context")
        if (
            reason == "idle"
            or has_attachment
            or len(people) > 1
            or any(len(_said(line.text)) >= _SUBSTANTIVE_CHARS for line in people)
        ):
            return Asked(stage="name")
        return Asked(why="opener_says_too_little")

    if not task.title_calibrated:
        if reason == "idle":
            return Asked(stage="calibrate")
        lines = await _conversation(session, task)
        if (
            sum(line.person and line.attachment is None for line in lines)
            >= _CALIBRATE_AFTER_PEOPLE
        ):
            return Asked(stage="calibrate")
        return Asked(why="few_people_since_it_opened")

    redis = get_redis_client()
    pending = reason == "signal" or (
        redis is not None
        and bool(await _redis_call(redis.exists, _redis_key("pending", task.id)))
    )
    if not pending:
        since = await _messages_since(session, task.id, task.title_checked_at)
        if since < settings.topic_naming_follow_messages:
            return Asked(why="nothing_new_since_last_check")
    if task.title_checked_at is not None and _now() - task.title_checked_at < timedelta(
        seconds=settings.topic_naming_follow_interval_seconds
    ):
        # Too soon; a pending signal waits for a later trigger.
        return Asked(why="checked_not_long_ago")
    if redis is not None:
        day = _redis_key(f"day:{_now():%Y%m%d}", task.id)
        count = await _redis_call(redis.incr, day)
        await _redis_call(redis.expire, day, 2 * 86400)
        if isinstance(count, int) and count > settings.topic_naming_follow_daily_limit:
            return Asked(why="daily_limit")
    return Asked(stage="follow")


# ---------- writing ----------


@dataclass(frozen=True)
class Renamed:
    task_id: uuid.UUID
    room_id: uuid.UUID
    title: str
    previous: str
    stage: str


async def _write(
    session: AsyncSession,
    task: Task,
    *,
    stage: Stage,
    verdict: Verdict,
) -> Renamed | None:
    """Apply a judgement if the title is still the one it was made against."""
    seen = task.title_version
    previous = task.title
    calibrated = stage != "name" or task.title_calibrated
    renamed = (
        not verdict.keep
        and verdict.title is not None
        and (stage == "name" or not same_title(verdict.title, previous))
    )
    guard = (
        Task.id == task.id,
        Task.title_version == seen,
        Task.title_source != TaskTitleSource.human,
    )
    if not renamed:
        if stage == "name":
            return None  # nothing usable; stay unnamed and try again later
        await session.execute(
            update(Task)
            .where(*guard)
            .values(title_checked_at=_now(), title_calibrated=True)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return None
    assert verdict.title is not None
    result = await session.execute(
        update(Task)
        .where(*guard)
        .values(
            title=verdict.title,
            title_source=TaskTitleSource.auto,
            title_version=seen + 1,
            title_checked_at=_now(),
            title_calibrated=calibrated,
        )
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 0) != 1:
        await session.rollback()
        return None  # someone renamed it meanwhile; theirs stands
    session.add(
        TaskTitle(
            task_id=task.id,
            title=verdict.title,
            source=TaskTitleSource.auto,
            reason=stage,
            by=None,
        )
    )
    await heard.inform(
        session,
        task,
        heard.task_renamed(
            by=None, title=verdict.title, previous="" if stage == "name" else previous
        ),
    )
    _tell_pages(session, task.id, task.room_id)
    await session.commit()
    return Renamed(
        task_id=task.id,
        room_id=task.room_id,
        title=verdict.title,
        previous=previous,
        stage=stage,
    )


def _tell_pages(session: AsyncSession, task_id: uuid.UUID, room_id: uuid.UUID) -> None:
    """Queue a frame for the channel's pages and for the task's own, which each
    listen on their own conversation, so both are told once this commits.

    Renaming writes the row with a bulk `update(Task)`, which the flush hook
    cannot see, so it is on this side to tell the pages (`room_task/live.py`).
    Nothing here needs the broker: whichever module stages it, `core/live_frames`
    hands the frame to the listener that does have one — telling the project's
    channel list along the way, since the frame names a ROOM."""
    frame = {"type": "state", "resource": "topics", "id": str(room_id)}
    for channel in (room_id, task_id):
        show_once_committed(session, channel, frame)


# ---------- entry points ----------


def _default_factory() -> SessionFactory:
    # deferred-import: tests replace this name on app.core.db
    from app.core.db import async_session_factory

    return async_session_factory


def _unasked(task_id: uuid.UUID, reason: Reason, why: str) -> None:
    """One line for a trigger that asked the model nothing, and why.

    A task that keeps its title is the quiet outcome by design, and quiet used
    to mean traceless: a room was once reported for never being named, and
    nothing anywhere said why (2026-09-27). This line is where that answer
    lives."""
    logger.info(
        "task naming: nothing asked task=%s reason=%s why=%s", task_id, reason, why
    )


async def run(
    conversation_id: uuid.UUID,
    reason: Reason,
    *,
    session_factory: SessionFactory | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Renamed | None:
    """Judge the title of the task ``conversation_id`` is, once, if this
    trigger calls for it. Any other conversation has nothing to name."""
    if not available():
        return None
    task_id = conversation_id
    redis = get_redis_client()
    factory = session_factory or _default_factory()
    async with factory() as session:
        task = await session.get(Task, task_id)
        if task is None or not _nameable(task):
            # A channel, a private chat, a closed task, a title a person chose:
            # naming has nothing to say here, and says nothing rather than one
            # line per message everywhere on the platform.
            return None
    if reason == "signal" and redis is not None:
        await _redis_call(redis.set, _redis_key("pending", task_id), "1", ex=7 * 86400)
    lock = _redis_key("lock", task_id)
    # After a failed call, give the gateway a couple of minutes before the next
    # message in the task asks again.
    if redis is not None and await _redis_call(
        redis.exists, _redis_key("backoff", task_id)
    ):
        _unasked(task_id, reason, "backing_off")
        return None
    if redis is not None:
        # SET NX answers None when someone else holds the lock — and so does a
        # Valkey that could not be reached, in which case the version check in
        # `_write` is protection enough and naming goes ahead.
        taken = await _redis_call(redis.set, lock, "1", nx=True, ex=90)
        if not taken and await _redis_call(redis.exists, lock):
            _unasked(task_id, reason, "another_trigger_is_running")
            return None
    renamed: Renamed | None = None
    try:
        async with factory() as session:
            task = await session.get(Task, task_id)
            if task is None or not _nameable(task):
                return None
            project = await session.get(Project, task.project_id)
            if naming_mode(project.settings if project else None) != "auto":
                _unasked(task_id, reason, "the_project_names_itself_manually")
                return None
            asked = await _stage(session, task, reason)
            if asked.stage is None:
                _unasked(task_id, reason, asked.why)
                return None
            stage = asked.stage
            material = await _material(session, task, stage)
            if material is None:
                _unasked(task_id, reason, "nothing_to_read")
                return None
            key = await service_key(session, _key_spec(), transport)
            if key is None:
                _unasked(task_id, reason, "no_gateway_key")
                return None
            # One budget for the whole judgement, asking again included.
            deadline = (
                asyncio.get_running_loop().time()
                + settings.topic_naming_timeout_seconds
            )
            answer = await _ask(
                key,
                _conversation_with_model(material),
                transport,
                timeout=settings.topic_naming_timeout_seconds,
            )
            await _record_usage(factory, answer, task)
            if answer.verdict is None:
                # An answer cut off before it said anything is not the gateway
                # failing, so the task is not made to wait out the backoff for
                # it: the next message asks again. Nothing was written, so the
                # task keeps its title either way.
                if not answer.truncated and redis is not None:
                    await _redis_call(
                        redis.set, _redis_key("backoff", task_id), "1", ex=120
                    )
                _unasked(
                    task_id,
                    reason,
                    "the_answer_was_cut_off" if answer.truncated else "the_call_failed",
                )
                return None
            verdict = await _fitted(
                key,
                material,
                answer.verdict,
                # At the first name the task has nothing of its own to keep.
                current=task.title if stage != "name" else None,
                transport=transport,
                factory=factory,
                deadline=deadline,
                task=task,
            )
            renamed = await _write(session, task, stage=stage, verdict=verdict)
            if stage == "follow" and redis is not None:
                await _redis_call(redis.delete, _redis_key("pending", task_id))
    finally:
        if redis is not None:
            await _redis_call(redis.delete, lock)
    return renamed


async def _task_of_document(document_id: uuid.UUID) -> uuid.UUID | None:
    async with _default_factory()() as session:
        return await session.scalar(
            select(Task.id).where(Task.document_id == document_id)
        )


_tasks: set[asyncio.Task] = set()


def _spawn(work) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        work.close()
        return
    task = loop.create_task(work)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def nudge(conversation_id: uuid.UUID, reason: Reason) -> None:
    """Something happened in ``conversation_id`` that may matter to the title
    of the task it is. A channel's or a private chat's is let go of at once.

    Fire and forget: the caller's request never waits on a model, and nothing
    it does can fail because naming did."""
    if not available():
        return
    _spawn(_run_quietly(conversation_id, reason))


def nudge_document(document_id: uuid.UUID) -> None:
    """A document was rewritten. When it is a task's, that is a moment the
    task's direction may show."""
    if not available():
        return
    _spawn(_run_quietly(None, "signal", document_id=document_id))


async def _run_quietly(
    conversation_id: uuid.UUID | None,
    reason: Reason,
    *,
    document_id: uuid.UUID | None = None,
) -> None:
    try:
        if conversation_id is None and document_id is not None:
            conversation_id = await _task_of_document(document_id)
        if conversation_id is not None:
            await run(conversation_id, reason)
    except Exception:  # naming never surfaces to anyone
        logger.warning(
            "task naming failed for %s", conversation_id or document_id, exc_info=True
        )
