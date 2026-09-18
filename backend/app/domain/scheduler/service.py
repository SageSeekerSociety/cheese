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

import httpx
from sqlalchemy import func, or_, select

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.github_app import github_app_read_token_for_project
from app.domain.block.models import AuthorType, Block
from app.domain.memory.dream import DREAM_PROMPT, latest_dream, open_dream
from app.domain.project.repositories import ProjectRepository
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.scheduler")

IDLE_MEMORY_HOURS = 8

# A poller against a network misses sometimes: a DNS blip, the connection owner
# restarting mid-release, a TLS handshake that never finished. The first miss is
# not news — the next tick is a minute away and usually fixes it — and reporting
# each one as an error made 39 of the alert channel's first 600 messages, none
# of which anybody acted on. What IS news is that the retries are not working,
# so a card has to miss this many ticks in a row before it is reported as an
# error. Everything else still fails loudly on the first occurrence: a bug in
# the poller is not something a later tick repairs.
TRANSIENT_MISSES_BEFORE_ERROR = 3


class SchedulerService:
    def __init__(self, *, chat_service: ChatService):
        self._chat = chat_service
        # Same DB binding as the chat service (real PG, or the test factory).
        self._sessions = chat_service.session_factory
        # Consecutive ticks each card has lost to the network, so that a blip
        # and an outage do not read the same. One instance drives every tick.
        self._transient_misses: dict[uuid.UUID, int] = {}

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

    async def remind_silent_turns(self) -> int:
        return await self._chat.remind_silent_turns()

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

    async def consolidate_idle_device_screens(
        self, idle_hours: float = IDLE_MEMORY_HOURS
    ) -> int:
        """Organize memory in quiet open rooms without releasing their agents.

        Archival cleanup owns resource deletion. Memory consolidation keeps its
        own activity and once-per-work-period checks, and remains opt-in.
        """
        from app.domain.agent.device_hub import device_hub
        from app.domain.topic.models import TopicStatus
        from app.domain.topic.services import TopicService

        pairs = {
            (s.project_id, s.topic_id)
            for s in device_hub.all_online_screens()
            if s.project_id is not None and s.topic_id is not None
        }
        cutoff = datetime.now(UTC) - timedelta(hours=idle_hours)
        dreams_started = 0
        async with self._sessions() as session:
            for project_id, topic_id in pairs:
                topic = await TopicService(session).get(topic_id)
                if topic is None or topic.status == TopicStatus.archived:
                    continue
                dream = await latest_dream(session, topic_id)
                last = await self._last_activity(session, topic_id, dream)
                if last is not None and last >= cutoff:
                    continue
                if dreams_started < settings.dream_max_per_sweep and (
                    await self._start_dream_if_worthwhile(
                        session, topic_id=topic_id, project_id=project_id, dream=dream
                    )
                ):
                    dreams_started += 1
        if dreams_started:
            logger.info("idle memory consolidation: started %d passes", dreams_started)
        return dreams_started

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
        """Give one quiet screen a turn to organize its memory. Return whether
        a pass was started; the screen remains allocated either way.

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
        failed: list[str] = []
        undispatched: list[str] = []
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
            if result.get("synced"):
                synced += 1
                continue
            if not result.get("conflicts"):
                # A failure that is not a conflict. `sync_upstream` reports those
                # as {"synced": False, "reason": ...} with NO "conflicts" key —
                # an expired App token, a 403, a fetch that blew its timeout, or
                # the fast-forward losing its compare-and-set race. The test
                # `not result.get("conflicts")` read every one of them as a
                # success, so the job's own line said it had brought N projects
                # current while some of them had not fetched a byte, and the
                # reason was dropped. A base that is quietly behind is what makes
                # an accept degrade to a local merge, which is the failure this
                # whole job exists to prevent.
                failed.append(f"{project.id}: {result.get('reason') or 'unknown'}")
                continue
            if not project.owner_handle:
                # A conflict with nobody to hand it to. Skipping is right — there
                # is no owner to ask — but this project stays behind until
                # somebody notices, so say which one rather than dropping it.
                undispatched.append(str(project.id))
                continue
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
        return {
            "synced": synced,
            "dispatched": dispatched,
            "failed": failed,
            "undispatched": undispatched,
            "errors": errors,
        }

    async def open_draft_prs(self) -> dict:
        """有东西就有 PR (#718 拍板①): give every batch with commits a draft PR,
        without waiting for anyone to file a card.

        The observation and every reason it is an observation rather than a hook
        live in `pr_publish.sweep_draft_prs`; this is only the clock.
        """
        from app.domain.review import pr_publish

        return dict(await pr_publish.sweep_draft_prs(self._sessions))

    async def poll_open_prs(self) -> dict:
        """Reconcile returned batches and advance pending PR cards (#718) one
        step — mirror its merge state, send the events the 「谁的活」 table
        names, and merge an armed auto-merge card whose rules are satisfied
        (AcceptService.advance_pr_card). One DB transaction per card so one
        card's failure can't roll back another's progress."""
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
                    self._transient_misses.pop(card_id, None)
                except Exception as exc:  # noqa: BLE001 — one card must not stop the rest
                    await session.rollback()
                    errors.append(f"{card_id}: {exc}")
                    self._report_card_failure(card_id, exc)
                    await self._note_card_poll_crashed(card_id, exc)
        return {"cards_checked": checked, "errors": errors}

    def _report_card_failure(self, card_id: uuid.UUID, exc: BaseException) -> None:
        """Loudly, unless the network is the only thing that went wrong and the
        retries have not yet run out of excuses."""
        if not isinstance(exc, httpx.TransportError):
            self._transient_misses.pop(card_id, None)
            logger.exception("poll_open_prs failed for card %s", card_id)
            return
        misses = self._transient_misses.get(card_id, 0) + 1
        self._transient_misses[card_id] = misses
        if misses < TRANSIENT_MISSES_BEFORE_ERROR:
            logger.warning(
                "poll_open_prs could not reach the network for card %s "
                "(%d in a row, reporting at %d): %r",
                card_id,
                misses,
                TRANSIENT_MISSES_BEFORE_ERROR,
                exc,
            )
            return
        logger.exception(
            "poll_open_prs has been unable to reach the network for card %s "
            "for %d ticks in a row",
            card_id,
            misses,
        )

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
