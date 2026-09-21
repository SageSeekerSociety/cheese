"""Resource usage data access + aggregation."""

import uuid

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage.models import ComputeGrant, ResourceUsage

# The share of a Space pool that means "warn the teacher". 80% is late enough
# that the number is a fact rather than a forecast, and early enough that
# topping up is still a choice — the alternative is everyone finding out by
# being refused.
SPACE_POOL_ALERT_RATIO = 0.8


def _require_positive_credits(credits_total: float) -> None:
    """Reject a top-up that is not a number we can charge against.

    NaN and infinity are the ones that matter: both survive SQLAlchemy's
    float bind, land in the ledger, and then make every comparison in
    ``consume`` false, so the pool silently stops refusing anything.
    """
    import math

    if not math.isfinite(credits_total) or credits_total <= 0:
        raise ValueError("credits must be finite and positive")


# The seven aggregates every usage roll-up reports — a room's, a project's, a
# whole Space's. Module-level rather than rebuilt per call so the one-query
# grouped roll-up and the single-owner one cannot drift apart.
#
# The public `turns` key is retained for wire compatibility, but its number now
# means distinct originating human messages or platform work ids, not intervals.
# One attributed unit can write several rows: the metering proxy logs every
# /v1/messages call and the gateway may land a deferred backfill. Rows without
# attribution still count once each.
#
# `unpriced_tokens` are tokens whose USD price is not knowable — a subscription
# is billed by the month, so its rows carry cost_usd = 0.0 meaning "no price",
# not "free". Reported separately so the UI can say 未知 instead of printing
# $0.0000 over millions of tokens ("未知冒充零").
_AGG_COLUMNS = (
    func.coalesce(func.sum(ResourceUsage.input_tokens), 0),
    func.coalesce(func.sum(ResourceUsage.output_tokens), 0),
    func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
    func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
    func.count(func.distinct(ResourceUsage.turn_id)),
    func.coalesce(
        func.sum(case((ResourceUsage.turn_id.is_(None), 1), else_=0)),
        0,
    ),
    func.coalesce(
        func.sum(
            case(
                (
                    (ResourceUsage.cost_usd <= 0.0) & (ResourceUsage.total_tokens > 0),
                    ResourceUsage.total_tokens,
                ),
                else_=0,
            )
        ),
        0,
    ),
)


def _agg_dict(row) -> dict:
    """One row of ``_AGG_COLUMNS`` as the wire shape the routes already return."""
    return {
        "input_tokens": int(row[0]),
        "output_tokens": int(row[1]),
        "total_tokens": int(row[2]),
        "cost_usd": float(row[3]),
        "turns": int(row[4]) + int(row[5]),
        "unpriced_tokens": int(row[6]),
    }


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
        # The ROOM's books. Every 分身 in a room spends through that room's one
        # session, so there is no second meter to read: a per-card figure would
        # be an invented split of one bill.
        row = ResourceUsage(
            project_id=project_id,
            topic_id=topic_id,
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
        stmt = select(*_AGG_COLUMNS).where(column == value)
        row = (await self._session.execute(stmt)).one()
        return _agg_dict(row)

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

    async def for_projects(self, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
        """Per-project spend in ONE query, for a Space's roll-up.

        The Space view needs a row per project and a total; asking
        ``for_project`` once per project would be a query per student project,
        which is a class-sized ``N+1``. Projects with no rows are absent from
        the result rather than present as zero — the caller knows its own
        roster and can fill the blanks, and inventing zeros here would claim
        the query saw projects it never matched.
        """
        if not project_ids:
            return {}
        stmt = (
            select(ResourceUsage.project_id, *_AGG_COLUMNS)
            .where(ResourceUsage.project_id.in_(project_ids))
            .group_by(ResourceUsage.project_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row[0]: _agg_dict(row[1:]) for row in rows}


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
        from app.domain.project.services import ProjectService

        row = ComputeGrant(
            team_id=await ProjectService(self._session).team_for_project(project_id),
            project_id=project_id,
            source_task_id=source_task_id,
            credits_total=credits_total,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def grant_team(self, team_id: int, credits_total: float) -> ComputeGrant:
        _require_positive_credits(credits_total)
        row = ComputeGrant(
            team_id=team_id,
            project_id=None,
            source_task_id=None,
            credits_total=credits_total,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def grant_space(self, space_id: int, credits_total: float) -> ComputeGrant:
        """Fund a Space's shared pool. Every project under the Space draws here.

        ``team_id`` stays NULL on purpose. A Space is not a team, and setting
        one would make the pool reachable by the team clause as well — the same
        credits spendable under two names, and reported twice by
        ``list_for_team``.
        """
        _require_positive_credits(credits_total)
        row = ComputeGrant(
            team_id=None,
            project_id=None,
            space_id=space_id,
            source_task_id=None,
            credits_total=credits_total,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_space(self, space_id: int) -> list[ComputeGrant]:
        """The Space's own pools, oldest first — what a teacher topped up."""
        result = await self._session.execute(
            select(ComputeGrant)
            .where(ComputeGrant.space_id == space_id)
            .order_by(ComputeGrant.created_at, ComputeGrant.id)
        )
        return list(result.scalars())

    async def space_summary(self, space_id: int) -> dict:
        """What the Space's pool has left, and whether it needs attention.

        Distinct from ``summary``: that one answers "what may this project
        spend" and folds in the project's earmarked and team grants; this one
        answers "how is the pool I funded doing" and counts only the Space's
        own rows. An empty pool is NOT unlimited the way a project with no
        grants is — a Space with no pool simply has none, and saying
        "unlimited" here would read as a promise the teacher never made.
        """
        grants = await self.list_for_space(space_id)
        total = sum(g.credits_total for g in grants)
        used = sum(g.credits_used for g in grants)
        return {
            "has_pool": bool(grants),
            "credits_total": total,
            "credits_used": used,
            "credits_remaining": total - used,
            "credits_ratio": (used / total) if total > 0 else 0.0,
            "exhausted": bool(grants) and used >= total,
            "needs_attention": (
                bool(grants) and total > 0 and used / total >= SPACE_POOL_ALERT_RATIO
            ),
            "grants": grants,
        }

    async def list_for_team(self, team_id: int) -> list[ComputeGrant]:
        from app.domain.project.services import ProjectService

        projects = await ProjectService(self._session).list_for_team(team_id)
        result = await self._session.execute(
            select(ComputeGrant)
            .where(
                or_(
                    ComputeGrant.team_id == team_id,
                    ComputeGrant.project_id.in_([p.id for p in projects]),
                )
            )
            .order_by(ComputeGrant.created_at, ComputeGrant.id)
        )
        return list(result.scalars())

    async def list_for_project(
        self, project_id: uuid.UUID, *, lock: bool = False
    ) -> list[ComputeGrant]:
        """Every grant this project may spend, in the order it must spend them.

        Three tiers, narrowest first:

        1. earmarked on this project (``project_id`` set) — a 赛题's resource
           pack, bought for this project alone;
        2. the Space's shared pool (``space_id`` set) — the course's one pool,
           which every project under the Space draws on;
        3. the team pool (neither set) — every project in the owning team.

        The order is the point, not a detail: a project must burn the credits
        bought for it before it burns the pool its classmates are also
        spending, or one team's profligacy silently eats the class's budget
        while its own earmarked credits sit untouched.

        A project made from the rail has no 赛题 and so no Space: tier 2 is
        simply absent for it, and it can never reach a course pool.
        """
        from app.domain.project.services import ProjectService

        projects = ProjectService(self._session)
        team_id = await projects.team_for_project(project_id)
        space_id = await projects.space_for_project(project_id)

        eligible = ComputeGrant.project_id == project_id
        if space_id is not None:
            eligible = or_(
                eligible,
                and_(
                    ComputeGrant.space_id == space_id,
                    ComputeGrant.project_id.is_(None),
                ),
            )
        if team_id is not None:
            eligible = or_(
                eligible,
                and_(
                    ComputeGrant.team_id == team_id,
                    ComputeGrant.project_id.is_(None),
                    # A Space pool is not a team pool even when the two would
                    # both match; without this the same row lands in two tiers.
                    ComputeGrant.space_id.is_(None),
                ),
            )

        # 0 = earmarked, 1 = Space, 2 = team. A CASE rather than the boolean
        # this used to be: `project_id IS NULL` can no longer tell the second
        # tier from the third, and ordering Space grants after team grants
        # would spend every class's shared budget before anyone's own.
        tier = case(
            (ComputeGrant.project_id.is_not(None), 0),
            (ComputeGrant.space_id.is_not(None), 1),
            else_=2,
        )
        stmt = (
            select(ComputeGrant)
            .where(eligible)
            .order_by(tier, ComputeGrant.created_at, ComputeGrant.id)
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return list((await self._session.execute(stmt)).scalars())

    async def summary(self, project_id: uuid.UUID) -> dict:
        """Team credits this project may spend, including its restricted grants.

        No applicable grant preserves the deployment's unmetered default.
        An exhausted team grant still exists, so new projects cannot bypass it.
        """
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
        """Deduct `credits` from eligible team/project grants. Any
        residual beyond all totals lands on the newest grant (credits_used may
        exceed credits_total) so recorded consumption stays truthful. Returns
        the amount deducted (0.0 when the project has no grants = unlimited)."""
        if credits <= 0:
            return 0.0
        grants = await self.list_for_project(project_id, lock=True)
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
