"""Project scheduler — the deterministic orchestration layer (spec §9.1).

"调度分两层：确定性的（定时任务/生命周期）= 平台代码做；需要判断的 = 芝士。"
This is the platform half: a background loop that periodically fires 定期巡检
(heartbeat) across all projects. 芝士 then makes the judgment calls inside each
heartbeat (该催谁/该拆什么/风险). Per-topic serialization lives in ChatService.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.github_app import github_app_read_token_for_project
from app.domain.block.models import AuthorType, Block
from app.domain.memory.dream import DREAM_PROMPT, latest_dream, open_dream
from app.domain.project.repositories import ProjectRepository
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
        """Parked — see docs/agent-principles.md §12.

        This drove 定期巡检: a timer woke 芝士 to look over every project and nudge
        whoever it judged to be behind. It pushes on a clock rather than on an
        event, so it either has nothing to say (a turn burned for nothing) or
        manufactures something (noise) — and everything it would notice (a topic
        stalled, a card waiting, a milestone due) is state the platform already
        knows the instant it changes. On dev it had produced zero notifications
        of its own in the product's lifetime.

        The need is real; a clock is the wrong trigger for it. Kept as a no-op
        rather than deleted so the runner wiring stays intact for whatever
        event-driven design replaces it.
        """
        return {"projects_inspected": 0, "errors": [], "parked": True}

    async def sweep_orphan_turns(self) -> int:
        """Periodic counterpart to the startup orphan sweep in `lifespan`.

        The startup one only ever runs when the PROCESS restarts, but a turn can
        die without taking the process with it (container recreate, OOM-killed
        child, sandbox image swap). Nothing re-read the registry in that case, so
        the topic stayed `active` forever — see AgentWorkRunner.sweep_orphans."""
        from app.api.deps import get_work_runner

        return await get_work_runner().sweep_orphans(
            self._chat,
            last_activity=self.last_block_at,
            silence_s=settings.turn_silence_timeout_s,
        )

    async def last_block_at(
        self, topic_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """Newest block timestamp per topic — the liveness probe the orphan sweep
        judges silence on. Lives here rather than in AgentWorkRunner because the runner
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
            # The column is TIMESTAMPTZ but some drivers hand back a naive
            # value, and a naive one would blow up the subtraction rather than
            # merely being wrong.
            out[topic_id] = (
                last if last.tzinfo is not None else last.replace(tzinfo=UTC)
            )
        return out

    async def reap_idle_device_screens(
        self, idle_hours: float = IDLE_REAP_HOURS
    ) -> int:
        """Close a device screen whose topic has had NO block activity for
        ``idle_hours`` — a topic that ran on a device and then went quiet used to
        leak its screen (and the ``claude`` process behind it) on the machine
        forever.

        Only ONLINE devices are walked (an offline box is unreachable now). Safe
        by construction: an active turn has just-persisted blocks, so its topic
        can never look idle. Teardown removes the device's per-topic tree.
        Returns how many topics were released.

        记忆整理 (issue #187) hangs here rather than on a clock of its own because
        this is the last moment a remembered claim can still be checked against
        the workspace it came from: `settings.dream_enabled` gives an
        about-to-die screen one turn to organize what the topic learned into the
        project's memory (see memory/dream.py). That pass runs INSIDE the screen,
        so the screen survives this sweep and is released by the next one — this
        loop is background maintenance and must never sit blocked for the minutes
        a model turn takes.

        A turn does NOT have to run on a device: it can run on Cloud, which
        leaves no screen behind, and this is the platform's only reaper. So
        dreaming reaches topics that ran on self-hosted devices and no others.
        That gap is known and accepted — closing it needs a Cloud-side reaper
        that does not exist yet, not a change here."""
        from app.domain.agent.device_hub import device_hub
        from app.domain.agent.device_provider import release_topic_screen

        pairs = {
            (s.project_id, s.topic_id)
            for s in device_hub.all_online_screens()
            if s.project_id is not None and s.topic_id is not None
        }
        if not pairs:
            return 0
        cutoff = datetime.now(UTC) - timedelta(hours=idle_hours)
        idle: list[tuple[uuid.UUID, uuid.UUID]] = []
        dreams_started = 0
        async with self._sessions() as session:
            for project_id, topic_id in pairs:
                dream = await latest_dream(session, topic_id)
                last = await self._last_activity(session, topic_id, dream)
                if last is not None and last >= cutoff:
                    continue  # recently active — keep the screen alive
                if dreams_started < settings.dream_max_per_sweep and (
                    await self._start_dream_if_worthwhile(
                        session,
                        topic_id=topic_id,
                        project_id=project_id,
                        dream=dream,
                    )
                ):
                    dreams_started += 1
                    continue  # organize now, release on the next sweep
                idle.append((project_id, topic_id))
        # Release outside the query session so teardown cannot hold it open.
        for project_id, topic_id in idle:
            await release_topic_screen(
                project_id, topic_id, session_factory=self._sessions
            )
        if dreams_started:
            logger.info(
                "idle screen reap: started %d 记忆整理 pass(es)", dreams_started
            )
        return len(idle)

    async def _last_activity(
        self, session, topic_id: uuid.UUID, dream
    ) -> datetime | None:
        """When this topic last did something that was NOT its own housekeeping.

        A 记忆整理 pass writes blocks, and blocks are what idleness is measured on
        — so counting them would have the screen renew its own lease off the very
        turn that was supposed to be its last, forever. The pass's turn id is on
        the dream row precisely so those blocks can be subtracted here; anything
        else in the topic, from anyone, still counts and still keeps the screen.

        Scope is the one topic, not the topic and its children. A screen is
        per-topic (so is the tree it works in, `~/.cheese/work/<project>/<topic>`),
        so a room and each of its 支线 hold separate screens with separate
        lifetimes and releasing one costs the others nothing."""
        stmt = select(func.max(Block.created_at)).where(Block.topic_id == topic_id)
        if dream is not None and dream.turn_id is not None:
            stmt = stmt.where(
                or_(Block.turn_id.is_(None), Block.turn_id != dream.turn_id)
            )
        last = (await session.execute(stmt)).scalar()
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return last

    async def _start_dream_if_worthwhile(
        self,
        session,
        *,
        topic_id: uuid.UUID,
        project_id: uuid.UUID,
        dream,
    ) -> bool:
        """Give one about-to-die screen a turn to organize its memory. True if a
        pass was started (and the screen therefore lives one more sweep).

        Everything here is a reason NOT to spend a turn, because the default has
        to be not spending one — the thing this repo already parked once was a
        clock that woke 芝士 with nothing to say."""
        if not settings.dream_enabled:
            return False
        if dream is not None and not await self._returned_to_life_since(
            session, topic_id, dream
        ):
            # Already organized (or already tried and failed). Re-running is how
            # a background trigger turns into an infinite loop, and "it failed,
            # so try again" is the same loop with a nicer story.
            return False
        blocks = (
            await session.execute(
                select(func.count())
                .select_from(Block)
                .where(Block.topic_id == topic_id)
            )
        ).scalar() or 0
        if blocks < settings.dream_min_blocks:
            return False  # nothing in here a later read of the transcript misses
        try:
            from app.api.deps import get_work_runner

            record = await open_dream(session, topic_id=topic_id, project_id=project_id)
            turn_id = get_work_runner().submit_kickoff(
                self._chat, topic_id, prompt=DREAM_PROMPT
            )
            record.turn_id = turn_id
            await session.commit()
        except Exception:  # noqa: BLE001 — 整理 must never hold up cleanup
            await session.rollback()
            logger.exception("记忆整理 failed to start for topic %s", topic_id)
            return False
        return True

    async def _returned_to_life_since(self, session, topic_id, dream) -> bool:
        """Did a PERSON come back to this topic after it was organized?

        Deliberately narrower than `_last_activity`: that one decides whether to
        release a screen (cheap and recoverable), this one decides whether to
        spend another model turn, and the two failure modes are not symmetric. A
        human block cannot be produced by a pass under any circumstance, so this
        answer cannot depend on the turn-id bookkeeping being perfect — which is
        what makes "organize a topic at most once" a guarantee rather than a
        hope."""
        found = (
            await session.execute(
                select(Block.id)
                .where(
                    Block.topic_id == topic_id,
                    Block.author_type == AuthorType.human,
                    Block.created_at > dream.created_at,
                )
                .limit(1)
            )
        ).scalar()
        return found is not None

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
        from app.api.deps import get_work_runner
        from app.domain.workspace import upstream_conflict

        runner = get_work_runner()
        synced = 0
        dispatched = 0
        errors: list[str] = []
        async with self._sessions() as session:
            projects = await ProjectRepository(session).list_all()
        for project in projects:
            try:
                if await asyncio.to_thread(ws.get_upstream, project.id) is None:
                    continue  # no upstream linked — nothing to keep current
                # A bound project fetches as the App; an unbound one fetches
                # with no credential, so a private upstream it is not bound to
                # fails here and is logged — never read on somebody else's key.
                async with self._sessions() as session:
                    token = await github_app_read_token_for_project(project.id, session)
                result = await asyncio.to_thread(
                    ws.sync_upstream, project.id, token=token
                )
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
        from app.api.deps import get_work_runner
        from app.domain.review.services import AcceptService

        runner = get_work_runner()
        checked = 0
        errors: list[str] = []
        async with self._sessions() as session:
            # 孤儿卡修复 (2026-08-10): cards on ARCHIVED topics are deliberately
            # NOT in this list — driving them means using the approver's GitHub
            # token on work nobody tracks any more. 那条判据留在 review 领域里
            # （open_pr_card_ids），调度器只管拿 id。
            card_ids = await AcceptService(session).open_pr_card_ids()
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
                    await self._note_card_poll_crashed(card_id, exc)
        return {"cards_checked": checked, "errors": errors}

    async def _note_card_poll_crashed(
        self, card_id: uuid.UUID, exc: BaseException
    ) -> None:
        """Leave the crash on the card, in its own transaction.

        The rollback above throws away everything the failed tick wrote — which
        is right for the state machine and wrong for the reader: the card keeps
        showing whatever it said before, usually 「等 CI」, while every tick dies
        the same way. A person watching a green PR that never merges has no way
        to tell that apart from slow checks. So the explanation is written by a
        SEPARATE session that the rollback cannot take with it.

        Best-effort by construction: if even this write fails, the log line
        above is still there and the poll loop keeps going.
        """
        from app.domain.review.services import AcceptService

        try:
            async with self._sessions() as session:
                await AcceptService(session).note_poll_crashed(card_id, exc)
                await session.commit()
        except Exception:  # noqa: BLE001 — never let the explanation kill the loop
            logger.exception("could not record poll failure on card %s", card_id)

    async def sweep_abandoned_gates(self) -> dict:
        """闸门孤儿卡扫底 (2026-08-11): condemn `pending_gate` cards whose gate
        runner is gone, so their topic stops being unable to file a new card.
        The actual rules (and why a periodic sweep is needed on top of the
        startup one) live in review/gate_sweep.py."""
        from app.api.deps import get_work_runner
        from app.domain.review import gate_sweep

        runner = get_work_runner()

        def nudge(topic_id: uuid.UUID, content: str, event: str, meta: dict) -> None:
            runner.submit(
                self._chat,
                topic_id,
                author="system",
                content=content,
                summon=True,
                nudge_event=event,
                nudge_meta=meta,
            )

        return await gate_sweep.sweep(self._sessions, nudge=nudge)

    async def sweep_conclusion_cards(self) -> dict:
        """结论卡·阶段一 (机制①bis): the 30-minute absolute timeout.

        The turn-end hook settles a card the moment the parent's digest turn
        finishes. This covers the case that hook cannot: the digest turn never
        ran at all (queued behind a wedged turn, refused on credits, killed by a
        deploy). 默认采信 must not depend on any turn actually happening.
        One transaction per sweep — the cards are independent but few.

        Second job, same shape: pay back the archives 采信 deferred because the
        sub-topic still held an undecided accept card. That deferral is what
        keeps a reviewer's card from being revoked out from under them; this is
        what keeps the deferral from turning into a never-archived sub-topic.

        Third job: land the sub-topic commits 采信 could not fold into the room's
        branch at the time — the room was waiting on CI, or somebody was editing
        in its workspace. Queuing those is the whole reason they are safe to
        refuse; this is the exit from the queue.
        """
        from app.domain.conclusion.services import ConclusionCardService

        errors: list[str] = []
        settled: list[uuid.UUID] = []
        archived: list[uuid.UUID] = []
        async with self._sessions() as session:
            try:
                settled = await ConclusionCardService(session).sweep_expired()
                if settled:
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 — maintenance must survive
                await session.rollback()
                logger.exception("conclusion card sweep failed")
                errors.append(str(exc))
        # 归档补账走**自己的**事务：默认采信是主机制，补账是它的尾巴，尾巴出错
        # 不能把已经结算好的卡一起回滚掉。
        async with self._sessions() as session:
            try:
                service = ConclusionCardService(session)
                archived = await service.sweep_deferred_archives()
                if archived:
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 — maintenance must survive
                await session.rollback()
                logger.exception("deferred archive sweep failed")
                errors.append(str(exc))
        # 幽灵额度: a backend that died mid-turn leaves a task marked running
        # forever, holding one of its room's four slots with nothing behind it.
        # Materialised residency is what lets a slot survive a restart; this is
        # the other half of that bargain.
        freed: list = []
        async with self._sessions() as session:
            try:
                from app.domain.room_task.services import ResidencyService

                svc = ResidencyService(session)
                freed = await svc.sweep_ghosts()
                for task in freed:
                    await svc.dequeue(task.room_id)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 — maintenance must survive
                await session.rollback()
                logger.exception("ghost residency sweep failed")
                errors.append(str(exc))
        return {
            "settled": len(settled),
            "archived": len(archived),
            "freed_slots": len(freed),
            "errors": errors,
        }
