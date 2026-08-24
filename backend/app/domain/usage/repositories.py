"""Resource usage data access + aggregation."""

import uuid

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.place import room_and_task
from app.domain.usage.models import ComputeGrant, ResourceUsage


class UsageRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        kind: str = "chat",
        metered: bool = True,
        route: str = "",
        turn_id: uuid.UUID | None = None,
    ) -> ResourceUsage:
        """Record spend against its originating message or platform work id.

        ``metered=False`` records work whose token counts are NOT knowable —
        the hooks backends run interactive Claude Code, which reports no usage
        locally, and the gateway that would supply it is not configured
        everywhere. Such work used to be skipped entirely, so the table showed
        an empty month while real money drained: 300 RMB of relay credit went
        without a single row naming what spent it. A row with zero tokens is
        still worth writing — it says work happened, on which project, with
        which model, which is the difference between "we do not know how much"
        and "we do not know anything".
        """
        # `topic_id` names the PLACE the spend happened in, which is usually a
        # thread — spend is what WORK does. Split here so "what did this task
        # cost" stays answerable, which a room-level total cannot answer.
        room_id, task_id = (
            (None, None)
            if topic_id is None
            else await room_and_task(self._session, topic_id)
        )
        row = ResourceUsage(
            project_id=project_id,
            topic_id=room_id,
            task_id=task_id,
            turn_id=turn_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost_usd,
            kind=kind if metered else f"{kind}:unmetered",
            route=route,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _agg(self, column, value) -> dict:
        # The public `turns` key is retained for wire compatibility, but its
        # number now means distinct originating human messages or platform work
        # ids, not intervals. One attributed unit can write several rows: the
        # metering proxy logs every /v1/messages call and the gateway may land a
        # deferred backfill. Rows without attribution still count once each.
        unattributed = func.sum(case((ResourceUsage.turn_id.is_(None), 1), else_=0))
        attributed_work = func.count(func.distinct(ResourceUsage.turn_id))
        # Tokens whose USD price is not knowable — a subscription is billed by
        # the month, so its rows carry cost_usd = 0.0 meaning "no price", not
        # "free". Reported separately so the UI can say 未知 instead of printing
        # $0.0000 over millions of tokens ("未知冒充零").
        unpriced = func.sum(
            case(
                (
                    (ResourceUsage.cost_usd <= 0.0) & (ResourceUsage.total_tokens > 0),
                    ResourceUsage.total_tokens,
                ),
                else_=0,
            )
        )
        stmt = select(
            func.coalesce(func.sum(ResourceUsage.input_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.output_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
            attributed_work,
            func.coalesce(unattributed, 0),
            func.coalesce(unpriced, 0),
        ).where(column == value)
        row = (await self._session.execute(stmt)).one()
        return {
            "input_tokens": int(row[0]),
            "output_tokens": int(row[1]),
            "total_tokens": int(row[2]),
            "cost_usd": float(row[3]),
            "turns": int(row[4]) + int(row[5]),
            "unpriced_tokens": int(row[6]),
        }

    async def for_topic(self, topic_id: uuid.UUID) -> dict:
        """A room's TOTAL — its own main line and every thread dispatched in it.

        Threads are included by construction rather than by a union: `add`
        stores the room in `topic_id` whichever half of the place the spend
        happened in, so this one predicate already reaches all of it.
        """
        return await self._agg(ResourceUsage.topic_id, topic_id)

    async def for_task(self, task_id: uuid.UUID) -> dict:
        """One thread's own spend, and nothing of the room around it."""
        return await self._agg(ResourceUsage.task_id, task_id)

    async def for_project(self, project_id: uuid.UUID) -> dict:
        return await self._agg(ResourceUsage.project_id, project_id)


class ComputeGrantRepository:
    """Compute-credit grants: issuance, balance, and deduction (spec §9.1)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def grant(
        self,
        *,
        project_id: uuid.UUID,
        source_task_id: int | None,
        credits_total: float,
    ) -> ComputeGrant:
        row = ComputeGrant(
            project_id=project_id,
            source_task_id=source_task_id,
            credits_total=credits_total,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_project(self, project_id: uuid.UUID) -> list[ComputeGrant]:
        stmt = (
            select(ComputeGrant)
            .where(ComputeGrant.project_id == project_id)
            .order_by(ComputeGrant.created_at, ComputeGrant.id)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def summary(self, project_id: uuid.UUID) -> dict:
        """Balance for a project. No grants at all = unlimited (spec §4: an
        unlinked, self-governing project is never metered)."""
        grants = await self.list_for_project(project_id)
        total = sum(g.credits_total for g in grants)
        used = sum(g.credits_used for g in grants)
        return {
            "unlimited": not grants,
            "credits_total": total,
            "credits_used": used,
            "credits_remaining": total - used,
            "grants": grants,
        }

    async def consume(self, project_id: uuid.UUID, credits: float) -> float:
        """Deduct `credits` across the project's grants, oldest first. Any
        residual beyond all totals lands on the newest grant (credits_used may
        exceed credits_total) so recorded consumption stays truthful. Returns
        the amount deducted (0.0 when the project has no grants = unlimited)."""
        if credits <= 0:
            return 0.0
        grants = await self.list_for_project(project_id)
        if not grants:
            return 0.0

        async def _charge(grant_id: uuid.UUID, amount: float) -> None:
            # Atomic increment: concurrent turns settling at once must never
            # lose a deduction to a read-modify-write race.
            await self._session.execute(
                update(ComputeGrant)
                .where(ComputeGrant.id == grant_id)
                .values(credits_used=ComputeGrant.credits_used + amount)
            )

        left = credits
        for g in grants:
            room = g.credits_total - g.credits_used
            if room <= 0:
                continue
            take = min(room, left)
            await _charge(g.id, take)
            left -= take
            if left <= 0:
                break
        if left > 0:  # overdraw: charge the newest grant, never lose usage
            await _charge(grants[-1].id, left)
        return credits
