"""Cloud compute, charged in credits (#2320 计费).

Cloud compute draws on the same credits as model calls: the same packs, the
same payer (``ledger.payer_for_project``), the same refusal when they run out.
It has no quota of its own; the platform's capacity cap is not the user's.

What runs is metered as runs (``ComputeRun``): one per stretch a cloud
sandbox, or a whole cloud VM, ran for a project, from start to stop. A run is
charged in whole minutes: every ``SETTLE_EVERY`` while it runs, and once more
when it ends, with its last partial minute rounded up. Each charge is one
usage row (route ``compute``, kind ``sandbox`` or ``vm``) and its deduction,
written together by ``Ledger.record``.

A cloud sandbox costs ``cloud_sandbox_credits_per_hour`` for every hour it
runs. Every sandbox has the same limits, so nothing is metered per CPU or
memory. A whole cloud VM costs its spec's price per hour
(``cloud_vm_credits_per_hour``). A user's own devices cost nothing and are
never metered.

There is no default price. With none set, ``admit_start`` refuses every start
and nothing is metered: cloud compute never runs free because nobody set a
price.

Which sandboxes are running is the machine domain's to say
(``machine.metering`` opens and closes their runs). A whole cloud VM opens its
run with ``ComputeMeter.open(kind=VM, spec=…)`` when it starts and closes it
with ``ComputeMeter.close`` when it stops, after ``admit_start`` let it start.
"""

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sentences import say
from app.domain.usage.ledger import Ledger, Payer, payer_for_project
from app.domain.usage.models import COMPUTE_ROUTE, ComputeRun

logger = logging.getLogger("cheese.usage.compute")

SANDBOX = "sandbox"
VM = "vm"
#: A running run is charged this often: often enough that a balance is never
#: far behind, rarely enough that a day's sandbox is a few dozen usage rows.
SETTLE_EVERY = timedelta(minutes=10)
_MINUTE = timedelta(minutes=1)
#: Runs charged per settlement pass.
SETTLE_BATCH = 200


class ComputeRefused(Exception):
    """Cloud compute may not start; the message is the sentence that says why."""


def hourly_price(kind: str, spec: str) -> float | None:
    """Credits per running hour of ``spec``; None when no price is set."""
    if kind == SANDBOX:
        return settings.cloud_sandbox_credits_per_hour
    return settings.cloud_vm_credits_per_hour.get(spec)


async def admit_start(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    kind: str = SANDBOX,
    spec: str = SANDBOX,
) -> None:
    """Whether cloud compute may start for this project now. Raises
    ``ComputeRefused`` when no price is set for it, or when the project's payer
    has no credits left."""
    if hourly_price(kind, spec) is None:
        logger.error(
            "cloud %s %r has no price: set CLOUD_SANDBOX_CREDITS_PER_HOUR "
            "(sandbox) or CLOUD_VM_CREDITS_PER_HOUR (vm); refusing to start it",
            kind,
            spec,
        )
        raise ComputeRefused(say("cloudComputeUnpriced"))
    payer = await payer_for_project(session, project_id)
    refusal = await Ledger(session).admit(payer)
    if refusal is not None:
        raise ComputeRefused(refusal.message)


class ComputeMeter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open(
        self,
        *,
        kind: str,
        spec: str,
        subject: str,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        at: datetime | None = None,
    ) -> None:
        """``subject`` started running at ``at`` (now). Nothing changes when
        a run of it is already open."""
        at = at or datetime.now(UTC)
        now = datetime.now(UTC)
        await self._session.execute(
            insert(ComputeRun)
            .values(
                id=uuid.uuid4(),
                kind=kind,
                spec=spec,
                subject=subject,
                project_id=project_id,
                topic_id=topic_id,
                session_id=session_id,
                started_at=at,
                billed_until=at,
                credits=0.0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["subject"],
                # A literal: the planner must see the predicate to match the
                # partial unique index.
                index_where=text("ended_at IS NULL"),
            )
        )

    async def close(self, subject: str, at: datetime | None = None) -> None:
        """``subject`` stopped running at ``at`` (now). Its run is charged to
        there by the next settlement."""
        at = at or datetime.now(UTC)
        run = await self._session.scalar(
            select(ComputeRun)
            .where(ComputeRun.subject == subject, ComputeRun.ended_at.is_(None))
            .with_for_update()
        )
        if run is not None:
            run.ended_at = max(at, run.started_at)
            await self._session.flush()

    async def open_runs(self, kind: str) -> list[ComputeRun]:
        return list(
            await self._session.scalars(
                select(ComputeRun).where(
                    ComputeRun.kind == kind, ComputeRun.ended_at.is_(None)
                )
            )
        )

    async def settle(self) -> int:
        """Charge every run that is due: a running one charged
        ``SETTLE_EVERY`` ago or longer, for its whole minutes since; an ended
        one, to its end, the last partial minute rounded up. Returns how many
        runs were charged. Commits."""
        now = datetime.now(UTC)
        runs = list(
            await self._session.scalars(
                select(ComputeRun)
                .where(
                    or_(
                        and_(
                            ComputeRun.ended_at.is_(None),
                            ComputeRun.billed_until <= now - SETTLE_EVERY,
                        ),
                        and_(
                            ComputeRun.ended_at.is_not(None),
                            ComputeRun.billed_until < ComputeRun.ended_at,
                        ),
                    )
                )
                .order_by(ComputeRun.billed_until)
                .limit(SETTLE_BATCH)
                .with_for_update(skip_locked=True)
            )
        )
        payers: dict[uuid.UUID, Payer] = {}
        ledger = Ledger(self._session)
        charged = 0
        for run in runs:
            price = hourly_price(run.kind, run.spec)
            if price is None:
                logger.error(
                    "compute run %s (%s %r) cannot be charged: no price is set",
                    run.id,
                    run.kind,
                    run.spec,
                )
                continue
            if run.ended_at is None:
                minutes = math.floor((now - run.billed_until) / _MINUTE)
            else:
                minutes = math.ceil((run.ended_at - run.billed_until) / _MINUTE)
            if minutes <= 0:
                continue
            if run.project_id not in payers:
                payers[run.project_id] = await payer_for_project(
                    self._session, run.project_id
                )
            credits = minutes * price / 60
            await ledger.record(
                payers[run.project_id],
                credits=credits,
                model=run.spec,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                route=COMPUTE_ROUTE,
                kind=run.kind,
                topic_id=run.topic_id,
            )
            await self._session.execute(
                update(ComputeRun)
                .where(ComputeRun.id == run.id)
                .values(
                    billed_until=run.billed_until + minutes * _MINUTE,
                    credits=ComputeRun.credits + credits,
                    updated_at=now,
                )
            )
            charged += 1
        await self._session.commit()
        return charged
