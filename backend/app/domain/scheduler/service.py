"""Project scheduler — the deterministic orchestration layer (spec §9.1).

"调度分两层：确定性的（定时任务/生命周期）= 平台代码做；需要判断的 = 芝士。"
This is the platform half: a background loop that periodically fires 定期巡检
(heartbeat) across all projects. 芝士 then makes the judgment calls inside each
heartbeat (该催谁/该拆什么/风险). Per-topic serialization lives in ChatService.
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.domain.agent.chat import ChatService
from app.domain.block.models import Block
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import Topic
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.scheduler")

# Idle-container reaper: a topic nobody has touched for this long gets its
# long-lived sandbox container(s) removed. The worktree + ~/.claude session live
# on host volumes, so the next turn simply recreates the box — nothing is lost.
# Covers topics that are never 采纳'd (the accept path already reaps its own).
IDLE_REAP_DAYS = 3
IDLE_REAP_EVERY_S = 24 * 3600  # at most one reap sweep per day


class SchedulerService:
    def __init__(self, *, chat_service: ChatService):
        self._chat = chat_service
        # Same DB binding as the chat service (real PG, or the test factory).
        self._sessions = chat_service.session_factory
        self._last_reap_mono = 0.0

    async def tick(self) -> dict:
        """One inspection round: run 定期巡检 on every project with a root topic."""
        async with self._sessions() as session:
            projects = await ProjectRepository(session).list_all()

        inspected = 0
        errors: list[str] = []
        for project in projects:
            if project.root_topic_id is None:
                continue
            try:
                await self._chat.run_heartbeat(project_id=project.id)
                inspected += 1
            except Exception as exc:  # one project's failure mustn't stop others
                errors.append(f"{project.id}: {exc}")

        # A machine provisioned from MicroCloud becomes usable only once it is
        # enrolled as a device, and that means SSHing into it — far too slow to
        # hang a request on, and it has to keep happening for a machine that came
        # up while nobody was looking.
        enrolled = 0
        try:
            enrolled = await self._enroll_ready_machines()
        except Exception:  # noqa: BLE001 — enrollment must never break the tick
            logger.exception("machine enrollment sweep failed")

        # Daily-throttled container reap rides the existing tick loop.
        if time.monotonic() - self._last_reap_mono >= IDLE_REAP_EVERY_S:
            self._last_reap_mono = time.monotonic()
            try:
                reaped = await self.reap_idle_containers()
                if reaped:
                    logger.info("idle reap: removed %d container(s)", reaped)
            except Exception:  # noqa: BLE001 — reaping must never break the tick
                logger.exception("idle container reap failed")
        return {
            "projects_inspected": inspected,
            "machines_enrolled": enrolled,
            "errors": errors,
        }

    async def _enroll_ready_machines(self) -> int:
        """Enroll machines that are up and wired but not yet cheese devices."""
        from app.domain.machine.services import MachineService

        async with self._sessions() as session:
            service = MachineService(session)
            if not service.available:
                return 0
            result = await service.enroll_pending()
            await session.commit()
        return result["enrolled"]

    async def reap_idle_containers(self, idle_days: int = IDLE_REAP_DAYS) -> int:
        """Remove sandbox containers whose topic has had NO block activity for
        ``idle_days`` (or whose topic no longer exists). Safe by construction: an
        active turn has just-persisted blocks, so its topic can never look idle."""
        names = ws.list_sandbox_containers()
        if not names:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=idle_days)
        reaped = 0
        async with self._sessions() as session:
            ids = (await session.execute(select(Topic.id))).scalars().all()
            by_hex = {t.hex[:12]: t for t in ids}
            for name in names:
                topic_id = by_hex.get(name.rsplit("-", 1)[-1])
                if topic_id is not None:
                    last = (
                        await session.execute(
                            select(func.max(Block.created_at)).where(
                                Block.topic_id == topic_id
                            )
                        )
                    ).scalar()
                    if last is not None and last.tzinfo is None:
                        last = last.replace(tzinfo=UTC)
                    if last is not None and last >= cutoff:
                        continue  # recently active — keep the box warm
                ws.remove_container(name)
                reaped += 1
        return reaped


class SchedulerRunner:
    """Background loop driving SchedulerService.tick() on an interval."""

    def __init__(self, scheduler: SchedulerService, interval_seconds: int):
        self._scheduler = scheduler
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("scheduler started (every %ss)", self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                result = await self._scheduler.tick()
                logger.info("scheduler tick: %s", result)
            except Exception:  # never let the loop die
                logger.exception("scheduler tick failed")
