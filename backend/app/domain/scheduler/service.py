"""Project scheduler — the deterministic orchestration layer (spec §9.1).

"调度分两层：确定性的（定时任务/生命周期）= 平台代码做；需要判断的 = 芝士。"
This is the platform half: a background loop that periodically fires 定期巡检
(heartbeat) across all projects. 芝士 then makes the judgment calls inside each
heartbeat (该催谁/该拆什么/风险). Per-topic serialization lives in ChatService.
"""

import asyncio
import contextlib
import logging

from app.domain.agent.chat import ChatService
from app.domain.project.repositories import ProjectRepository

logger = logging.getLogger("cheesex.scheduler")


class SchedulerService:
    def __init__(self, *, chat_service: ChatService):
        self._chat = chat_service
        # Same DB binding as the chat service (real PG, or the test factory).
        self._sessions = chat_service.session_factory

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
        return {"projects_inspected": inspected, "errors": errors}


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
