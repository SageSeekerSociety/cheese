"""Project scheduler — the deterministic orchestration layer (spec §9.1).

"调度分两层：确定性的（定时任务/生命周期）= 平台代码做；需要判断的 = 芝士。"
This is the platform half: a background loop that periodically fires 定期巡检
(heartbeat) across all projects. 芝士 then makes the judgment calls inside each
heartbeat (该催谁/该拆什么/风险). Per-topic serialization lives in ChatService.
"""

import asyncio
import contextlib
import logging
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
IDLE_REAP_HOURS = 8


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

    async def reap_idle_containers(self, idle_hours: float = IDLE_REAP_HOURS) -> int:
        """Remove sandbox containers whose topic has had NO block activity for
        ``idle_hours`` (or whose topic no longer exists). Safe by construction:
        an active turn has just-persisted blocks, so its topic can never look
        idle.

        Reaping is not destructive to the conversation. The transcript lives in
        the topic's ``~/.claude`` mount on the HOST, so the next turn recreates
        the box and resumes from it — the container is the body, not the
        continuity."""
        names = ws.list_sandbox_containers()
        if not names:
            return 0
        cutoff = datetime.now(UTC) - timedelta(hours=idle_hours)
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

    async def poll_open_prs(self) -> dict:
        """两阶段采纳 (PR迭代式, 2026-08-09): advance every pr_open accept card
        one step — see AcceptService.advance_pr_card for the actual state
        machine (check PR CI → merge → check deploy workflow → archive).
        One DB transaction per card so one card's failure can't roll back
        another's progress."""
        from app.api.deps import get_turn_runner
        from app.domain.review.models import AcceptStatus
        from app.domain.review.repositories import AcceptCardRepository
        from app.domain.review.services import AcceptService

        runner = get_turn_runner()
        checked = 0
        errors: list[str] = []
        async with self._sessions() as session:
            cards = await AcceptCardRepository(session).list_by_status(
                AcceptStatus.pr_open
            )
            card_ids = [c.id for c in cards]
        for card_id in card_ids:
            async with self._sessions() as session:
                try:
                    await AcceptService(session).advance_pr_card(
                        card_id, chat_service=self._chat, runner=runner
                    )
                    await session.commit()
                    checked += 1
                except Exception as exc:  # noqa: BLE001 — one card must not stop the rest
                    await session.rollback()
                    errors.append(f"{card_id}: {exc}")
                    logger.exception("poll_open_prs failed for card %s", card_id)
        return {"cards_checked": checked, "errors": errors}


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


class SandboxReaperRunner:
    """Deterministic sandbox cleanup, independent from AI heartbeat scheduling."""

    def __init__(
        self,
        scheduler: SchedulerService,
        interval_seconds: int,
        idle_hours: float = IDLE_REAP_HOURS,
    ):
        self._scheduler = scheduler
        self._interval = interval_seconds
        self._idle_hours = idle_hours
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info(
                "sandbox reaper started (every %ss, idle>%sh)",
                self._interval,
                self._idle_hours,
            )

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
                reaped = await self._scheduler.reap_idle_containers(self._idle_hours)
                if reaped:
                    logger.info("idle reap: removed %d container(s)", reaped)
            except Exception:  # noqa: BLE001 -- maintenance loop must survive
                logger.exception("idle container reap failed")


class PrPollRunner:
    """两阶段采纳 (PR迭代式, 2026-08-09): drives SchedulerService.poll_open_prs()
    on an interval, independent from the AI heartbeat and the idle reaper —
    same shape as SandboxReaperRunner."""

    def __init__(self, scheduler: SchedulerService, interval_seconds: int):
        self._scheduler = scheduler
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("PR poll runner started (every %ss)", self._interval)

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
                result = await self._scheduler.poll_open_prs()
                if result["cards_checked"] or result["errors"]:
                    logger.info("pr poll: %s", result)
            except Exception:  # noqa: BLE001 -- maintenance loop must survive
                logger.exception("pr poll failed")
