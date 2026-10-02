"""Credits: who pays for a model call, whether it may run, and what it costs
(#2397).

Every credit pack (``ComputeGrant``) belongs to a team. A person's own credits
are packs on their personal team, so a call outside any project and a call in
one of the person's own projects both draw on the same packs, whoever in the
room asked for it. A team's project draws on that team.

A call names its payer: the team that pays, and, where it is known, the person
it is made for, whose member packs may also pay. A charge drains the packs that
apply in this order:

1. what a task earmarked for this project;
2. the paying team's plan share for this person;
3. the paying team's plan pack for the period;
4. credits bought for this person, then the team's own bought and granted ones.

A member pack applies on its own team, and on the member's personal team: what
a team gives a member, the member may spend on their own work too. The last
call may overdraw; it was admitted with credits left, and what it spent is
spent either way.

Whether a payer holding no pack at all may run is ``settings.credits_unlimited``,
a deployment's explicit choice rather than what a missing row happens to mean.
"""

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.usage.credits import usage_to_credits
from app.domain.usage.models import ComputeGrant, GrantSource
from app.domain.usage.repositories import UsageRepository

# Where a month starts and ends for the people using the platform.
_TZ = ZoneInfo("Asia/Shanghai")


def month_of(moment: datetime) -> date:
    local = moment.astimezone(_TZ)
    return date(local.year, local.month, 1)


def month_end(month: date) -> datetime:
    """When the month's plan pack lapses and the next month's becomes available."""
    following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return datetime(following.year, following.month, 1, tzinfo=_TZ)


@dataclass(frozen=True)
class Rates:
    """USD per token for the model a call will use: fresh prompt tokens, output
    tokens, prompt tokens read from the provider's cache, and prompt tokens
    written to it."""

    input: float
    output: float
    cache_read: float
    cache_write: float

    def cost_usd(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """What the gateway bills for a call, the way it bills it:
        ``input_tokens`` counts every prompt token, cached ones included, and
        the cached share is billed at its own rate."""
        fresh = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        return (
            fresh * self.input
            + cache_read_tokens * self.cache_read
            + cache_write_tokens * self.cache_write
            + output_tokens * self.output
        )

    @classmethod
    def of(
        cls, model: str, table: Mapping[str, tuple[float, float, float, float]] | None
    ) -> "Rates | None":
        """``model``'s rates from the gateway's rate table
        (``feature_stats.pricing.model_rates``); None when a call to it cannot
        be charged."""
        if not settings.llm_gateway_credit_usd or not table or model not in table:
            return None
        return cls(*table[model])


@dataclass(frozen=True)
class Payer:
    """Who pays for a call: the team, and the person it is made for when that
    is known. ``personal`` says the team is that person's personal team, where
    the packs every team gave them may be spent too."""

    team_id: int
    person_id: int | None = None
    project_id: uuid.UUID | None = None
    personal: bool = False


async def payer_for_person(session: AsyncSession, user_id: int) -> Payer:
    """A call a person makes outside any project: their personal team pays."""
    from app.domain.team.services import team_service

    team = await team_service(session).ensure_personal_team(user_id)
    return Payer(team_id=team.id, person_id=user_id, personal=True)


async def payers_for_projects(
    session: AsyncSession, projects: list
) -> dict[uuid.UUID, Payer]:
    """Each project's payer: its team, and for a project on a personal team,
    that team's owner — whoever in the room made the call."""
    from app.domain.team.services import team_service

    teams = await team_service(session).get_teams_by_ids(
        sorted({p.team_id for p in projects})
    )
    out: dict[uuid.UUID, Payer] = {}
    for project in projects:
        team = teams.get(project.team_id)
        owner = team.personal_owner_user_id if team is not None else None
        out[project.id] = Payer(
            team_id=project.team_id,
            person_id=owner,
            project_id=project.id,
            personal=owner is not None,
        )
    return out


async def payer_for_project(session: AsyncSession, project_id: uuid.UUID) -> Payer:
    from app.domain.project.services import ProjectService

    project = await ProjectService(session).get_or_404(project_id)
    return (await payers_for_projects(session, [project]))[project.id]


def _personal_plan_held_back(pack: ComputeGrant, payer: Payer) -> bool:
    """Until plans land (#2397, PR B), a personal team's monthly plan pack
    pays only for what its owner asks outside a project: their projects run as
    they did before it existed. PR B deletes this rule."""
    return (
        payer.personal
        and payer.project_id is not None
        and pack.source == GrantSource.PLAN_PERIOD
    )


def _applies(pack: ComputeGrant, payer: Payer) -> bool:
    if _personal_plan_held_back(pack, payer):
        return False
    if pack.project_id is not None:
        return pack.project_id == payer.project_id
    if pack.member_user_id is not None:
        return pack.member_user_id == payer.person_id and (
            pack.team_id == payer.team_id or payer.personal
        )
    return pack.team_id == payer.team_id


def _live(pack: ComputeGrant, now: datetime) -> bool:
    return pack.expires_at is None or pack.expires_at > now


def _rank(pack: ComputeGrant) -> int:
    if pack.project_id is not None:
        return 0
    if pack.source == GrantSource.TEAM_MEMBER_PLAN:
        return 1
    if pack.source == GrantSource.PLAN_PERIOD:
        return 2
    if pack.member_user_id is not None:
        return 3
    return 4


_FAR = datetime.max.replace(tzinfo=UTC)


def _order(pack: ComputeGrant) -> tuple:
    # Within a rank, what lapses first is spent first.
    return (_rank(pack), pack.expires_at or _FAR, pack.created_at, pack.id)


@dataclass(frozen=True)
class Balance:
    unlimited: bool
    credits_total: float
    credits_used: float
    # When the payer's plan next issues a pack; None when no plan pack applies.
    resets_at: datetime | None
    # Every pack the payer could ever have drawn on, lapsed ones included:
    # the gateway's brake compares against a key's lifetime spend.
    ever_granted: float = 0.0
    packs: tuple[ComputeGrant, ...] = ()

    @property
    def credits_remaining(self) -> float:
        return self.credits_total - self.credits_used

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.credits_remaining <= 0

    def exhausted_message(self) -> str:
        at = self.resets_at
        if at is None:
            return "芝士额度已用完。"
        at = at.astimezone(_TZ)
        return f"本月的芝士额度已用完，{at.month}月{at.day}日重置。"

    def retry_after_s(self) -> int:
        """Seconds a refused caller should wait: until the plan's next pack,
        and an hour when nothing will be issued on its own."""
        if self.resets_at is None:
            return 3600
        return max(60, int((self.resets_at - datetime.now(UTC)).total_seconds()))

    def summary(self) -> dict:
        """The shape the credit endpoints have always answered with."""
        return {
            "unlimited": self.unlimited,
            "credits_total": self.credits_total,
            "credits_used": self.credits_used,
            "credits_remaining": self.credits_remaining,
            "ever_granted": self.ever_granted,
            "grants": list(self.packs),
        }


_PLANS = (GrantSource.PLAN_PERIOD, GrantSource.TEAM_MEMBER_PLAN)


def _spendable(candidates: Iterable[ComputeGrant], payer: Payer) -> list:
    """Of ``candidates``, what ``payer`` may spend now, in the order a charge
    drains them."""
    now = datetime.now(UTC)
    return sorted(
        (p for p in candidates if _applies(p, payer) and _live(p, now)), key=_order
    )


def _balance(candidates: list[ComputeGrant], payer: Payer) -> Balance:
    packs = _spendable(candidates, payer)
    expiring = [
        p.expires_at for p in packs if p.source in _PLANS and p.expires_at is not None
    ]
    return Balance(
        unlimited=not packs and settings.credits_unlimited,
        credits_total=sum(p.credits_total for p in packs),
        credits_used=sum(p.credits_used for p in packs),
        resets_at=min(expiring) if expiring else None,
        ever_granted=sum(p.credits_total for p in candidates if _applies(p, payer)),
        packs=tuple(packs),
    )


class Ledger:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---- reading -----------------------------------------------------------

    async def _candidates(
        self, payers: Iterable[Payer], *, lock: bool = False
    ) -> list[ComputeGrant]:
        """Every pack any of ``payers`` might draw on, expired ones included;
        ``_applies`` and ``_live`` narrow them per payer."""
        payers = list(payers)
        projects = {p.project_id for p in payers if p.project_id is not None}
        teams = {p.team_id for p in payers}
        people = {p.person_id for p in payers if p.person_id is not None}
        conditions = [
            and_(
                ComputeGrant.team_id.in_(teams),
                ComputeGrant.project_id.is_(None),
                ComputeGrant.member_user_id.is_(None),
            )
        ]
        if projects:
            conditions.append(ComputeGrant.project_id.in_(projects))
        if people:
            conditions.append(ComputeGrant.member_user_id.in_(people))
        stmt = select(ComputeGrant).where(or_(*conditions))
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return list((await self._session.execute(stmt)).scalars())

    async def _payer_candidates(
        self, payer: Payer, *, lock: bool = False
    ) -> list[ComputeGrant]:
        # A person asking outside any project is the moment their personal
        # team's plan pack for the month comes into being.
        if payer.project_id is None and payer.personal:
            await self._plan_pack(payer.team_id)
        return await self._candidates([payer], lock=lock)

    async def balance(self, payer: Payer) -> Balance:
        return _balance(await self._payer_candidates(payer), payer)

    async def balances(self, payers: Mapping[uuid.UUID, Payer]) -> dict:
        """``balance`` for many payers at once, in one query."""
        candidates = await self._candidates(payers.values())
        return {key: _balance(candidates, payer) for key, payer in payers.items()}

    async def earmarks(self, project_id: uuid.UUID) -> list[ComputeGrant]:
        """Every pack ever earmarked for one project, lapsed ones included."""
        rows = await self._session.execute(
            select(ComputeGrant).where(ComputeGrant.project_id == project_id)
        )
        return list(rows.scalars())

    async def team_packs(self, team_id: int) -> list[ComputeGrant]:
        """Every live pack held by ``team_id``, its earmarks and member packs too."""
        now = datetime.now(UTC)
        rows = await self._session.execute(
            select(ComputeGrant)
            .where(ComputeGrant.team_id == team_id)
            .order_by(ComputeGrant.created_at, ComputeGrant.id)
        )
        return [p for p in rows.scalars() if _live(p, now)]

    # ---- charging ----------------------------------------------------------

    async def charge(self, payer: Payer, credits: float) -> float:
        """Deduct ``credits`` from ``payer``'s packs in order. What no pack has
        room for lands on the last one, or, with none live, on the newest pack
        the payer ever had, so recorded consumption stays truthful. Returns the
        credits deducted."""
        if credits <= 0:
            return 0.0
        candidates = await self._payer_candidates(payer, lock=True)
        packs = _spendable(candidates, payer)
        if not packs:
            everything = [p for p in candidates if _applies(p, payer)]
            if not everything:
                return 0.0
            packs = [max(everything, key=lambda p: (p.created_at, p.id))]
        left = credits
        for pack in packs:
            room = pack.credits_total - pack.credits_used
            if room <= 0:
                continue
            take = min(room, left)
            await self._deduct(pack.id, take)
            left -= take
            if left <= 0:
                break
        if left > 0:
            await self._deduct(packs[-1].id, left)
        return credits

    async def _deduct(self, pack_id: uuid.UUID, amount: float) -> None:
        # An increment, not a write of a value read earlier: two calls settling
        # at once must both be deducted.
        await self._session.execute(
            update(ComputeGrant)
            .where(ComputeGrant.id == pack_id)
            .values(credits_used=ComputeGrant.credits_used + amount)
        )

    async def charge_priced(
        self,
        payer: Payer,
        *,
        model: str,
        rates: Rates,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        kind: str,
    ) -> float:
        """Record one call made outside any project, priced at ``rates``, and
        charge it. Returns the credits deducted."""
        cost = rates.cost_usd(
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        )
        return await self.charge_spent(
            payer,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            kind=kind,
        )

    async def charge_spent(
        self,
        payer: Payer,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        kind: str,
    ) -> float:
        """Record what the gateway says a call outside any project spent, and
        charge it. Returns the credits deducted."""
        row = await UsageRepository(self._session).add(
            project_id=None,
            user_id=payer.person_id,
            topic_id=None,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            kind=kind,
            route="gateway",
        )
        return await self.charge(payer, usage_to_credits(row, spend_priced=True))

    # ---- issuing -----------------------------------------------------------

    async def _plan_pack(self, team_id: int) -> None:
        """This month's plan pack for a personal team, written the first time
        it is needed, so two first requests racing each other still write one."""
        month = month_of(datetime.now(UTC))
        await self._session.execute(
            insert(ComputeGrant)
            .values(
                id=uuid.uuid4(),
                team_id=team_id,
                source=GrantSource.PLAN_PERIOD.value,
                period_start=month,
                expires_at=month_end(month),
                credits_total=settings.personal_credits_monthly,
                credits_used=0.0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                index_elements=["team_id", "period_start"],
                # A literal, not a bound parameter: the planner must see the
                # predicate to match the partial unique index.
                index_where=text("source = 'plan_period'"),
            )
        )

    async def grant_earmark(
        self,
        *,
        project_id: uuid.UUID,
        source_task_id: int | None,
        credits_total: float,
    ) -> ComputeGrant:
        """A task's credits for one project, spendable only there."""
        from app.domain.project.services import ProjectService

        project = await ProjectService(self._session).get_or_404(project_id)
        return await self._add(
            ComputeGrant(
                team_id=project.team_id,
                project_id=project_id,
                source=GrantSource.TASK_EARMARK.value,
                source_task_id=source_task_id,
                credits_total=credits_total,
            )
        )

    async def grant(
        self,
        team_id: int,
        credits_total: float,
        *,
        source: GrantSource = GrantSource.ADMIN_GRANT,
        member_user_id: int | None = None,
        expires_at: datetime | None = None,
    ) -> ComputeGrant:
        if not math.isfinite(credits_total) or credits_total <= 0:
            raise ValueError("credits must be finite and positive")
        return await self._add(
            ComputeGrant(
                team_id=team_id,
                member_user_id=member_user_id,
                source=source.value,
                expires_at=expires_at,
                credits_total=credits_total,
            )
        )

    async def _add(self, pack: ComputeGrant) -> ComputeGrant:
        pack.credits_used = 0.0
        self._session.add(pack)
        await self._session.flush()
        return pack
