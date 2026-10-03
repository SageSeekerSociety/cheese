"""What a turn waits for before it starts, and what the room is told meanwhile.

Two things can be full. The project's slots (``max_concurrent_turns``) are the
project's share of the platform. The session host is what every project's
sessions share: one machine, one kernel. A session started there when it has no
memory left does not fail alone — the machine stalls in swap and takes every
room on it, and whatever else runs beside them, down together (#1544). So a turn
that would start a new session on the host waits until the host has room for
one more session at its cap, and the room reads the same queue notice it reads
when the project is full.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.sentences import say
from app.domain.agent.platform_notices import (
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)

logger = logging.getLogger(__name__)

#: The variable the launcher reads to start a session under its memory cap.
SESSION_MEMORY_ENV = "CHEESE_SESSION_MEMORY_MAX"
#: How long one reading of the host's memory answers for. Turns admitted in a
#: burst share it rather than each asking the machine.
READING_TTL_S = 2.0
#: How often a turn held for the host's memory looks again.
RECHECK_S = 5.0

#: 排队不是故障：平台自己会往前推，没人需要动手。
QUEUED_META = notice(
    EVENT_TURN_QUEUED,
    severity=SEVERITY_INFO,
    who=WHO_PLATFORM,
    detail=say("turnQueuedDetail"),
    detail_label=say("labelWhatHappensNext"),
)
#: 整机内存不够时排队：前面不一定有别的轮次，等的是资源。
HOST_BUSY_META = notice(
    EVENT_TURN_QUEUED,
    severity=SEVERITY_INFO,
    who=WHO_PLATFORM,
    detail=say("turnQueuedHostBusyDetail"),
    detail_label=say("labelWhatHappensNext"),
)


def queued_text(ahead: int) -> str:
    """Platform copy for a turn the project's slots hold back."""
    if ahead <= 0:
        return say("turnQueued")
    return say("turnQueuedBehind", ahead=ahead)


def session_memory_max(device_id: str) -> str | None:
    """The cap a session started on ``device_id`` runs under, as systemd spells
    it; None on any machine but the session host, which is the only one whose
    kernel every room shares."""
    mb = settings.agent_session_memory_max_mb
    if mb <= 0 or device_id != settings.agent_session_device_id:
        return None
    return f"{mb}M"


def available_bytes(meminfo: str) -> int | None:
    """``MemAvailable`` out of ``/proc/meminfo``: what the kernel can hand a new
    process without swapping, page cache it can drop included."""
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


class HostMemory:
    """Whether the session host has room for the session a turn would start.

    A room whose session is already running on the host starts nothing, so it
    is never held: holding it would stop the rooms that are using the memory
    from finishing with it. A host that cannot be read (not Linux, unreachable)
    holds nobody either — the gate exists to keep the host up, and a turn that
    cannot reach the host fails on its own with the reason.
    """

    def __init__(self, hub=None):
        self._hub = hub
        self._lock = asyncio.Lock()
        self._read_at = float("-inf")
        self._available: int | None = None

    async def has_room(self, topic_id: uuid.UUID) -> bool:
        host = settings.agent_session_device_id
        mb = settings.agent_session_memory_max_mb
        if not host or mb <= 0:
            return True
        hub = self._hub
        if hub is None:
            from app.domain.agent.device_hub import device_hub as hub
        if any(screen.device_id == host for screen in hub.screens_for_topic(topic_id)):
            return True
        available = await self._read(hub, host)
        return available is None or available >= mb * 1024 * 1024

    async def can_start(self, mb: int) -> bool:
        """Whether the session host has ``mb`` free for a session that is not
        running yet (a person's 芝士). Unreadable holds nobody, as above."""
        host = settings.agent_session_device_id
        if not host:
            return True
        hub = self._hub
        if hub is None:
            from app.domain.agent.device_hub import device_hub as hub
        available = await self._read(hub, host)
        return available is None or available >= mb * 1024 * 1024

    async def _read(self, hub, host: str) -> int | None:
        async with self._lock:
            if time.monotonic() - self._read_at < READING_TTL_S:
                return self._available
            try:
                result = await hub.exec(host, ["cat", "/proc/meminfo"], timeout=5)
                self._available = (
                    available_bytes(result.get("stdout") or "")
                    if result.get("exit") == 0
                    else None
                )
            except Exception:  # noqa: BLE001 — an unread host holds nobody
                logger.warning("session host memory unreadable", exc_info=True)
                self._available = None
            self._read_at = time.monotonic()
            return self._available


async def wait_for_host(
    has_room: Callable[[uuid.UUID], Awaitable[bool]] | None,
    topic_id: uuid.UUID,
    tell_room: Callable[[str], Awaitable[object]],
) -> None:
    """Hold a turn until the host has room, telling its room once that it waits."""
    told = False
    while has_room is not None and not await has_room(topic_id):
        if not told:
            told = True
            await tell_room(say("turnQueuedHostBusy"))
        await asyncio.sleep(RECHECK_S)
