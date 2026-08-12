"""Project scheduler — the deterministic orchestration layer (spec §9.1).

"调度分两层：确定性的（定时任务/生命周期）= 平台代码做；需要判断的 = 芝士。"
This is the platform half: a background loop that periodically fires 定期巡检
(heartbeat) across all projects. 芝士 then makes the judgment calls inside each
heartbeat (该催谁/该拆什么/风险). Per-topic serialization lives in ChatService.
"""

import asyncio
import contextlib
import logging
import uuid
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

    async def sweep_orphan_turns(self) -> int:
        """Periodic counterpart to the startup orphan sweep in `lifespan`.

        The startup one only ever runs when the PROCESS restarts, but a turn can
        die without taking the process with it (container recreate, OOM-killed
        child, sandbox image swap). Nothing re-read the registry in that case, so
        the topic stayed `active` forever — see TurnRunner.sweep_orphans."""
        from app.api.deps import get_turn_runner
        from app.core.config import settings

        return await get_turn_runner().sweep_orphans(
            self._chat,
            last_activity=self.last_block_at,
            silence_s=settings.turn_silence_timeout_s,
        )

    async def last_block_at(
        self, topic_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """Newest block timestamp per topic — the liveness probe the orphan sweep
        judges silence on. Lives here rather than in TurnRunner because the runner
        has no DB binding, and it is the same signal a human reads off the topic
        (「最后一块是几点」), which is what makes a sweep verdict checkable."""
        if not topic_ids:
            return {}
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(Block.topic_id, func.max(Block.created_at))
                    .where(Block.topic_id.in_(topic_ids))
                    .group_by(Block.topic_id)
                )
            ).all()
        out: dict[uuid.UUID, datetime] = {}
        for topic_id, last in rows:
            if last is None:
                continue
            # Same normalization as reap_idle_containers: the column is TIMESTAMPTZ
            # but some drivers hand back a naive value, and a naive one would blow
            # up the subtraction rather than merely being wrong.
            out[topic_id] = (
                last if last.tzinfo is not None else last.replace(tzinfo=UTC)
            )
        return out

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

    async def sync_upstreams(self) -> dict:
        """Keep every linked project's base current with its upstream, unattended.

        同步上游 has only ever been a button someone presses. Nobody presses it,
        the platform's base falls behind the upstream's default branch, and then
        accepting stops being able to push: GitHub rejects any branch whose
        `.github/workflows/` differs from the default branch unless the
        credential carries `workflows` (see `push_topic_branch_for_github_pr`,
        whose comment reads "Nearly every card hit this"). The card then
        degrades to a local merge and the work never leaves the platform.

        Measured on 2026-08-11: three accepts in a row degraded that way; one
        manual 同步上游 later, the next four opened PRs normally. Falling behind
        is the whole cause, and staying current is something a loop can do — so
        this is that loop.

        Conflicts hand off exactly as the manual button does. `dispatch()`
        reuses an already-open resolution task, so repeating this on an interval
        cannot pile up duplicates, and a project with no owner is skipped rather
        than dispatched into nowhere."""
        from app.api.deps import get_turn_runner
        from app.domain.workspace import upstream_conflict

        runner = get_turn_runner()
        synced = 0
        dispatched = 0
        errors: list[str] = []
        async with self._sessions() as session:
            projects = await ProjectRepository(session).list_all()
        for project in projects:
            try:
                if await asyncio.to_thread(ws.get_upstream, project.id) is None:
                    continue  # no upstream linked — nothing to keep current
                result = await asyncio.to_thread(ws.sync_upstream, project.id)
            except Exception as exc:  # noqa: BLE001 — one project must not stop the rest
                errors.append(f"{project.id}: {exc}")
                logger.exception("upstream sync failed for project %s", project.id)
                continue
            if result.get("synced") or not result.get("conflicts"):
                synced += 1
                continue
            if not project.owner_handle:
                continue  # nobody to hand the conflict to
            async with self._sessions() as session:
                handoff = await upstream_conflict.dispatch(
                    session,
                    project.id,
                    requested_by=project.owner_handle,
                    chat=self._chat,
                    runner=runner,
                )
                await session.commit()
            if handoff is not None:
                dispatched += 1
        return {"synced": synced, "dispatched": dispatched, "errors": errors}

    async def poll_open_prs(self) -> dict:
        """两阶段采纳 (PR迭代式, 2026-08-09): advance every pr_open accept card
        one step — see AcceptService.advance_pr_card for the actual state
        machine (check PR CI → merge → check deploy workflow → archive).
        One DB transaction per card so one card's failure can't roll back
        another's progress."""
        from app.api.deps import get_turn_runner
        from app.domain.review.repositories import AcceptCardRepository
        from app.domain.review.services import AcceptService

        runner = get_turn_runner()
        checked = 0
        errors: list[str] = []
        async with self._sessions() as session:
            # 孤儿卡修复 (2026-08-10): cards on ARCHIVED topics are deliberately
            # NOT in this list — driving them means using the approver's GitHub
            # token on work nobody tracks any more.
            cards = await AcceptCardRepository(session).list_pr_open_on_active_topics()
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

    async def sweep_abandoned_gates(self) -> dict:
        """闸门孤儿卡扫底 (2026-08-11): condemn `pending_gate` cards whose gate
        runner is gone, so their topic stops being unable to file a new card.
        The actual rules (and why a periodic sweep is needed on top of the
        startup one) live in review/gate_sweep.py."""
        from app.api.deps import get_turn_runner
        from app.domain.review import gate_sweep

        runner = get_turn_runner()

        def nudge(topic_id: uuid.UUID, content: str) -> None:
            runner.submit(
                self._chat, topic_id, author="system", content=content, summon=True
            )

        return await gate_sweep.sweep(self._sessions, nudge=nudge)

    async def sweep_conclusion_cards(self) -> dict:
        """结论卡·阶段一 (机制①bis): the 30-minute absolute timeout.

        The turn-end hook settles a card the moment the parent's digest turn
        finishes. This covers the case that hook cannot: the digest turn never
        ran at all (queued behind a wedged turn, refused on credits, killed by a
        deploy). 默认采信 must not depend on any turn actually happening.
        One transaction per sweep — the cards are independent but few.
        """
        from app.domain.conclusion.services import ConclusionCardService

        async with self._sessions() as session:
            try:
                settled = await ConclusionCardService(session).sweep_expired()
                if settled:
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 — maintenance must survive
                await session.rollback()
                logger.exception("conclusion card sweep failed")
                return {"settled": 0, "errors": [str(exc)]}
        return {"settled": len(settled), "errors": []}


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


class OrphanSweepRunner:
    """Drives SchedulerService.sweep_orphan_turns() on its own interval — same
    shape as PrPollRunner. Cheap: it reads one small JSON file and does nothing
    unless it finds a registered turn the process is not running."""

    def __init__(self, scheduler: SchedulerService, interval_seconds: int):
        self._scheduler = scheduler
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("orphan sweep runner started (every %ss)", self._interval)

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
                resumed = await self._scheduler.sweep_orphan_turns()
                if resumed:
                    logger.info("orphan sweep: %s turn(s) resumed", resumed)
            except Exception:  # noqa: BLE001 -- maintenance loop must survive
                logger.exception("orphan sweep failed")


class UpstreamSyncRunner:
    """Drives SchedulerService.sync_upstreams() on its own interval — same shape
    as PrPollRunner. Separate from the AI heartbeat on purpose: staying current
    with upstream is deterministic plumbing, not a judgment call."""

    def __init__(self, scheduler: SchedulerService, interval_seconds: int):
        self._scheduler = scheduler
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("upstream sync runner started (every %ss)", self._interval)

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
                result = await self._scheduler.sync_upstreams()
                if result["synced"] or result["dispatched"] or result["errors"]:
                    logger.info("upstream sync: %s", result)
            except Exception:  # noqa: BLE001 -- maintenance loop must survive
                logger.exception("upstream sync failed")


class GateSweepRunner:
    """闸门孤儿卡扫底 (2026-08-11): drives SchedulerService.sweep_abandoned_gates()
    on an interval — same shape as PrPollRunner.

    The startup sweep in `app.main.lifespan` covers cards orphaned by a
    restart; this loop covers the other half — the gate task dying while the
    process keeps running (see review/gate_sweep.py). Without it the ceiling on
    "how long a topic stays unable to file a card" is "until the next redeploy".
    """

    def __init__(self, scheduler: SchedulerService, interval_seconds: int):
        self._scheduler = scheduler
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("gate sweep runner started (every %ss)", self._interval)

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
                result = await self._scheduler.sweep_abandoned_gates()
                if result["condemned"] or result["errors"]:
                    logger.info("gate sweep: %s", result)
            except Exception:  # noqa: BLE001 -- maintenance loop must survive
                logger.exception("gate sweep failed")


class ConclusionSweepRunner:
    """结论卡·阶段一: drives SchedulerService.sweep_conclusion_cards() on an
    interval — same shape as PrPollRunner. Its whole job is making sure 默认采信
    happens even when no turn ever ends."""

    def __init__(self, scheduler: SchedulerService, interval_seconds: int):
        self._scheduler = scheduler
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("conclusion sweep runner started (every %ss)", self._interval)

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
                result = await self._scheduler.sweep_conclusion_cards()
                if result["settled"] or result["errors"]:
                    logger.info("conclusion sweep: %s", result)
            except Exception:  # noqa: BLE001 -- maintenance loop must survive
                logger.exception("conclusion sweep failed")
