"""What a conversation is shown while its AI teammate's runtime downloads onto
a person's own computer.

The first time a teammate works on a computer, the install fetches the
executor, a download of a hundred megabytes and more that a slow link takes
minutes over, while the tool call that asked for the computer waits. The
people in the conversation would otherwise see nothing happen at all. So the
download's progress, which the install writes to stderr
(`executor_download.progress`) and the connector streams up while the install
runs (``exec``'s ``on_stderr``), is shown as one platform line in the
conversation the teammate works in. That line is updated in place as the
download advances, and ends saying the runtime is installed, or how far it got.

One line per computer and conversation, not one per attempt: an attempt cut
off at its deadline keeps what it downloaded, and the next one picks up the
same line where the last left it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.live_frames import show_once_committed
from app.core.sentences import say, with_keys
from app.domain.agent.announce import announce
from app.domain.agent_session.models import AgentSession
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.conversation.services import room_of
from app.domain.device.wiring import sql_device_service
from app.domain.notice_meta import EVENT_EXECUTOR_INSTALL

logger = logging.getLogger(__name__)

#: The most often the line is rewritten while the download runs. The install
#: says how far it got every two seconds; a room is told at most this often.
SHOW_EVERY_S = 3.0

_DOWNLOADING, _INSTALLED, _STOPPED, _FAILED = (
    "downloading",
    "installed",
    "stopped",
    "failed",
)


def downloaded(line: str) -> tuple[int, int] | None:
    """``(have, total)`` bytes if ``line`` is the install saying how far its
    download has got (`executor_download.progress`), else None."""
    if not line.startswith('{"executor_download"'):
        return None
    try:
        said = json.loads(line)["executor_download"]
        return int(said["have"]), int(said["total"])
    except (ValueError, KeyError, TypeError):
        return None


def _mb(count: int) -> int:
    return round(count / 1e6)


class InstallProgress:
    """The line one install shows in its conversation.

    ``heard`` takes each line the install writes to stderr as it arrives;
    entered as a context manager around the install, the line is written as
    the download advances, at most every ``SHOW_EVERY_S``, and settled when
    the install ends. An install that downloads nothing shows nothing."""

    def __init__(
        self, bind: AsyncEngine, *, session_id: uuid.UUID, device_id: str
    ) -> None:
        self._bind = bind
        self._session_id = session_id
        self._device_id = device_id
        self._latest: tuple[int, int] | None = None
        self._news = asyncio.Event()
        self._ended = asyncio.Event()
        self._shown: uuid.UUID | None = None
        self._showing: asyncio.Task[None] | None = None

    def heard(self, line: str) -> None:
        """Called on the device link's read loop: returns at once."""
        progress = downloaded(line)
        if progress is not None:
            self._latest = progress
            self._news.set()

    async def __aenter__(self) -> Callable[[str], None]:
        self._showing = asyncio.create_task(self._show())
        return self.heard

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self._showing is not None
        # A write in flight finishes first: the settled line must not race it.
        self._ended.set()
        self._news.set()
        (shown,) = await asyncio.gather(self._showing, return_exceptions=True)
        if shown is not None:
            logger.error("install progress line not written", exc_info=shown)
        if self._latest is None:
            return
        have, total = self._latest
        try:
            if exc is None:
                await self._write(_INSTALLED, have, total)
            elif have < total:
                await self._write(_STOPPED, have, total)
            else:
                await self._write(_FAILED, have, total, detail=_last_line(exc))
        except Exception:
            # The line says how the install went; it does not decide it. An
            # install that worked still hands over its executor, and one that
            # failed still answers with its own failure.
            logger.exception("install progress line not settled")

    async def _show(self) -> None:
        while True:
            await self._news.wait()
            self._news.clear()
            if self._ended.is_set():
                return
            assert self._latest is not None
            await self._write(_DOWNLOADING, *self._latest)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._ended.wait(), SHOW_EVERY_S)

    async def _write(
        self, state: str, have: int, total: int, *, detail: str | None = None
    ) -> None:
        async with AsyncSession(self._bind, expire_on_commit=False) as db:
            line = await self._line(db)
            if line is None:
                return
            conversation_id, device = line
            done, size = _mb(have), _mb(total)
            content = {
                _DOWNLOADING: say(
                    "executorInstallDownloading", device=device, done=done, total=size
                ),
                _INSTALLED: say("executorInstallDone", device=device),
                _STOPPED: say(
                    "executorInstallStopped", device=device, done=done, total=size
                ),
                _FAILED: say("executorInstallFailed", device=device),
            }[state]
            meta = {
                "event_type": EVENT_EXECUTOR_INSTALL,
                "severity": "info" if state in (_DOWNLOADING, _INSTALLED) else "warn",
                # The platform is at it while it downloads; after that nobody
                # has anything to do about this line (no `who`: `announce`
                # reads a missing one as the platform's, and tells nobody).
                "who": "platform" if state == _DOWNLOADING else None,
                "device_id": self._device_id,
                "state": state,
                "have": have,
                "total": total,
                **({"detail": detail} if detail else {}),
            }
            block = await self._open(db, conversation_id)
            if block is None:
                room = await room_of(db, conversation_id)
                block = await announce(
                    db,
                    place_id=room,
                    task_id=None if conversation_id == room else conversation_id,
                    content=content,
                    meta=meta,
                )
            else:
                block.content = content
                block.meta = with_keys(meta, content=content)
                await db.flush()
                show_once_committed(
                    db,
                    block.conversation_id,
                    {
                        "type": "block_updated",
                        "block": BlockOut.model_validate(block).model_dump(mode="json"),
                    },
                )
            await db.commit()
            if block is not None:
                self._shown = block.id

    async def _line(self, db: AsyncSession) -> tuple[uuid.UUID, str] | None:
        """The conversation the session works in and the computer's name."""
        session = await db.get(AgentSession, self._session_id)
        device = await sql_device_service(db).get_device(self._device_id)
        if session is None or device is None:
            return None
        return session.conversation_id, device.name

    async def _open(self, db: AsyncSession, conversation_id: uuid.UUID) -> Block | None:
        """The line to rewrite: this install's own, or the one an earlier
        install on the same computer left unfinished in this conversation."""
        if self._shown is not None:
            return await db.get(Block, self._shown)
        return await db.scalar(
            select(Block)
            .where(
                Block.conversation_id == conversation_id,
                Block.kind == BlockKind.event,
                Block.meta["event_type"].as_string() == EVENT_EXECUTOR_INSTALL,
                Block.meta["device_id"].as_string() == self._device_id,
                Block.meta["state"].as_string() != _INSTALLED,
            )
            .order_by(Block.created_at.desc())
            .limit(1)
        )


def _last_line(exc: BaseException) -> str | None:
    """What stopped the install, in its own last words, for the details."""
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return lines[-1] if lines else None
