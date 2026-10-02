"""Credits: who pays for a model call, whether it may run, and what it costs
(#2397).

Credits belong only to teams, never to a person inside one. A person's own
credits are packs on their personal team. Where a call happens decides which
team pays: a team's project, that team; a call outside any project or in the
person's own project, their personal team — including when someone else in
that project's room summons 芝士.

A charge drains the packs that apply in this order:

1. what a task earmarked for this project;
2. the paying team's plan pack for the period;
3. credits bought or granted by an administrator, what lapses first first.

The last call may overdraw; it was admitted with credits left, and what it
spent is spent either way.

Every team is on a plan (``Plan``). A plan issues its pack for the month the
first time a call of that month is charged; until then the balance counts it
as already there, so reading a balance never writes. A plan may cap what a team
spends within time windows: once a window is full, only bought or granted
credits may be spent until it reopens. An unlimited plan refuses nothing, issues
nothing and draws on nothing; its usage is still recorded.
"""

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.block.notice_text import NoticeText, say
from app.domain.usage.credits import usage_to_credits
from app.domain.usage.models import ComputeGrant, GrantSource, Plan
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
class Terms:
    """What a team's plan says about spending."""

    key: str
    unlimited: bool = False
    credits_per_period: float | None = None
    # ``(hours, credits)``: at most that many credits within that many hours.
    windows: tuple[tuple[float, float], ...] = ()
    # The model tiers the plan allows; None is every tier.
    model_tiers: frozenset[str] | None = None

    @classmethod
    def of(cls, plan: Plan) -> "Terms":
        return cls(
            key=plan.key,
            unlimited=plan.unlimited,
            credits_per_period=plan.credits_per_period,
            windows=tuple(
                (float(w["hours"]), float(w["credits"])) for w in plan.windows or []
            ),
            model_tiers=(
                None if plan.model_tiers is None else frozenset(plan.model_tiers)
            ),
        )

    @property
    def issues(self) -> float:
        """The pack the plan issues each month; 0 when it issues none."""
        if self.unlimited or not self.credits_per_period:
            return 0.0
        return float(self.credits_per_period)


async def terms_of(session: AsyncSession, plan_keys: Iterable[str]) -> dict:
    """Plan key → ``Terms``, for every key given."""
    keys = sorted(set(plan_keys))
    if not keys:
        return {}
    rows = await session.execute(select(Plan).where(Plan.key.in_(keys)))
    return {plan.key: Terms.of(plan) for plan in rows.scalars()}


async def team_terms(session: AsyncSession, team_id: int) -> Terms:
    """The terms of the plan a team is on."""
    from app.domain.team.services import team_service

    team = await team_service(session).get_team(team_id)
    key = team.plan_key if team is not None else "free"
    return (await terms_of(session, [key]))[key]


@dataclass(frozen=True)
class Payer:
    """The team that pays for a call, the project it happens in if any, whether
    that team is someone's personal team, and what its plan allows."""

    team_id: int
    terms: Terms
    project_id: uuid.UUID | None = None
    personal: bool = False


async def payer_for_person(session: AsyncSession, user_id: int) -> Payer:
    """A call a person makes outside any project: their personal team pays."""
    from app.domain.team.services import team_service

    team = await team_service(session).ensure_personal_team(user_id)
    terms = (await terms_of(session, [team.plan_key]))[team.plan_key]
    return Payer(team_id=team.id, terms=terms, personal=True)


async def payers_for_projects(
    session: AsyncSession, projects: list
) -> dict[uuid.UUID, Payer]:
    """Each project's payer: its team, whoever in the room made the call."""
    from app.domain.team.services import team_service

    teams = await team_service(session).get_teams_by_ids(
        sorted({p.team_id for p in projects})
    )
    terms = await terms_of(session, [t.plan_key for t in teams.values()] + ["free"])
    out: dict[uuid.UUID, Payer] = {}
    for project in projects:
        team = teams.get(project.team_id)
        out[project.id] = Payer(
            team_id=project.team_id,
            terms=terms[team.plan_key if team is not None else "free"],
            project_id=project.id,
            personal=team is not None and team.personal_owner_user_id is not None,
        )
    return out


async def payer_for_project(session: AsyncSession, project_id: uuid.UUID) -> Payer:
    from app.domain.project.services import ProjectService

    project = await ProjectService(session).get_or_404(project_id)
    return (await payers_for_projects(session, [project]))[project.id]


def _applies(pack: ComputeGrant, payer: Payer) -> bool:
    if pack.project_id is not None:
        return pack.project_id == payer.project_id
    return pack.team_id == payer.team_id


def _live(pack: ComputeGrant, now: datetime) -> bool:
    return pack.expires_at is None or pack.expires_at > now


def _rank(pack: ComputeGrant) -> int:
    if pack.project_id is not None:
        return 0
    if pack.source == GrantSource.PLAN_PERIOD:
        return 1
    return 2


_FAR = datetime.max.replace(tzinfo=UTC)

#: What a team may still spend once a plan's time window is full.
_BOUGHT = (GrantSource.PURCHASE, GrantSource.ADMIN_GRANT)


@dataclass(frozen=True)
class Refusal:
    """Why a call may not run, and when it may."""

    message: NoticeText
    reopens_at: datetime | None

    def retry_after_s(self) -> int:
        """Seconds a refused caller should wait, an hour when nothing will
        change on its own."""
        if self.reopens_at is None:
            return 3600
        return max(60, int((self.reopens_at - datetime.now(UTC)).total_seconds()))


def _hours(hours: float) -> str:
    return str(int(hours)) if float(hours).is_integer() else f"{hours:g}"


def _day(at: datetime) -> dict:
    local = at.astimezone(_TZ)
    return {"month": local.month, "day": local.day, "time": local.strftime("%H:%M")}


def _order(pack: ComputeGrant) -> tuple:
    # Within a rank, what lapses first is spent first.
    return (_rank(pack), pack.expires_at or _FAR, pack.created_at, pack.id)


@dataclass(frozen=True)
class Balance:
    unlimited: bool
    credits_total: float
    credits_used: float
    # When the payer's plan next issues a pack; None when it issues none.
    resets_at: datetime | None
    packs: tuple[ComputeGrant, ...] = ()

    @property
    def credits_remaining(self) -> float:
        return self.credits_total - self.credits_used

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.credits_remaining <= 0

    def bought_remaining(self) -> float:
        return sum(
            p.credits_total - p.credits_used for p in self.packs if p.source in _BOUGHT
        )

    def refusal(self) -> Refusal:
        if self.resets_at is None:
            return Refusal(say("creditsSpent"), None)
        at = self.resets_at.astimezone(_TZ)
        return Refusal(
            say("creditsMonthSpent", month=at.month, day=at.day), self.resets_at
        )

    def summary(self) -> dict:
        """The shape the credit endpoints have always answered with."""
        return {
            "unlimited": self.unlimited,
            "credits_total": self.credits_total,
            "credits_used": self.credits_used,
            "credits_remaining": self.credits_remaining,
            "grants": list(self.packs),
        }


def _spendable(candidates: Iterable[ComputeGrant], payer: Payer) -> list:
    """Of ``candidates``, what ``payer`` may spend now, in the order a charge
    drains them."""
    now = datetime.now(UTC)
    return sorted(
        (p for p in candidates if _applies(p, payer) and _live(p, now)), key=_order
    )


def _issued_this_month(candidates: Iterable[ComputeGrant], payer: Payer) -> bool:
    month = month_of(datetime.now(UTC))
    return any(
        p.source == GrantSource.PLAN_PERIOD
        and p.team_id == payer.team_id
        and p.period_start == month
        for p in candidates
    )


def _balance(candidates: list[ComputeGrant], payer: Payer) -> Balance:
    packs = _spendable(candidates, payer)
    # The month's plan pack counts before its first charge writes it.
    unissued = 0.0
    if payer.terms.issues and not _issued_this_month(candidates, payer):
        unissued = payer.terms.issues
    resets_at = None
    if payer.terms.issues:
        resets_at = month_end(month_of(datetime.now(UTC)))
    return Balance(
        unlimited=payer.terms.unlimited,
        credits_total=sum(p.credits_total for p in packs) + unissued,
        credits_used=sum(p.credits_used for p in packs),
        resets_at=resets_at,
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
        conditions = [
            and_(
                ComputeGrant.team_id.in_(teams),
                ComputeGrant.project_id.is_(None),
            )
        ]
        if projects:
            conditions.append(ComputeGrant.project_id.in_(projects))
        stmt = select(ComputeGrant).where(or_(*conditions))
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return list((await self._session.execute(stmt)).scalars())

    async def balance(self, payer: Payer) -> Balance:
        return _balance(await self._candidates([payer]), payer)

    async def admit(self, payer: Payer) -> Refusal | None:
        """Whether a call ``payer`` would pay for may run now; None if it may.

        A full time window leaves only bought or granted credits to spend; with
        none, the call waits for the window to reopen."""
        if payer.terms.unlimited:
            return None
        balance = await self.balance(payer)
        full = await self._full_window(payer)
        if full is not None:
            if balance.bought_remaining() > 0:
                return None
            hours, reopens_at = full
            return Refusal(
                say("creditsWindowFull", hours=_hours(hours), **_day(reopens_at)),
                reopens_at,
            )
        if balance.exhausted:
            return balance.refusal()
        return None

    async def _full_window(self, payer: Payer) -> tuple[float, datetime] | None:
        """The plan's window that is full, and when it reopens: the longest
        wait when several are."""
        full: tuple[float, datetime] | None = None
        now = datetime.now(UTC)
        repo = UsageRepository(self._session)
        for hours, cap in payer.terms.windows:
            used, oldest = await repo.team_window(
                payer.team_id, since=now - timedelta(hours=hours)
            )
            if used < cap or oldest is None:
                continue
            reopens_at = oldest + timedelta(hours=hours)
            if full is None or reopens_at > full[1]:
                full = (hours, reopens_at)
        return full

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

    async def live_packs(self, team_ids: list[int]) -> dict[int, list[ComputeGrant]]:
        """Every live pack each of ``team_ids`` holds, oldest first."""
        if not team_ids:
            return {}
        now = datetime.now(UTC)
        rows = await self._session.execute(
            select(ComputeGrant)
            .where(ComputeGrant.team_id.in_(team_ids))
            .order_by(ComputeGrant.created_at, ComputeGrant.id)
        )
        out: dict[int, list[ComputeGrant]] = {}
        for pack in rows.scalars():
            if _live(pack, now):
                out.setdefault(pack.team_id, []).append(pack)
        return out

    async def team_packs(self, team_id: int) -> list[ComputeGrant]:
        """Every live pack held by ``team_id``, its project earmarks too."""
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
        if credits <= 0 or payer.terms.unlimited:
            return 0.0
        if payer.terms.issues:
            await self._plan_pack(payer.team_id, payer.terms.issues)
        candidates = await self._candidates([payer], lock=True)
        packs = _spendable(candidates, payer)
        if packs and await self._full_window(payer) is not None:
            # A full window leaves only bought credits; with none left, the
            # call was admitted on what remained and lands in the usual order.
            packs = [p for p in packs if p.source in _BOUGHT] or packs
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
        user_id: int,
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
            user_id=user_id,
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
        user_id: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        kind: str,
    ) -> float:
        """Record what the gateway says a call ``user_id`` made outside any
        project spent, and charge it. Returns the credits deducted."""
        spent = SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd
        )
        return await self.record(
            payer,
            credits=usage_to_credits(spent, spend_priced=True),
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            kind=kind,
            route="gateway",
        )

    async def record(
        self,
        payer: Payer,
        *,
        credits: float,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        route: str,
        kind: str = "chat",
        topic_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
        user_id: int | None = None,
        metered: bool = True,
    ) -> float:
        """Write one usage row, naming the team that paid and the credits it
        cost, and charge those credits: the row and the deduction cannot come
        apart. Every usage row is written here, an unmetered one (work done,
        tokens unknown) too, so each names the team it belongs to. Returns the
        credits deducted."""
        await UsageRepository(self._session).add(
            project_id=payer.project_id,
            topic_id=topic_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            kind=kind,
            route=route,
            turn_id=turn_id,
            user_id=user_id,
            team_id=payer.team_id,
            credits=credits,
            metered=metered,
        )
        return await self.charge(payer, credits)

    # ---- issuing -----------------------------------------------------------

    async def _plan_pack(self, team_id: int, credits: float) -> None:
        """This month's plan pack, written the first time a call of the month
        is charged, so two first charges racing each other still write one."""
        month = month_of(datetime.now(UTC))
        await self._session.execute(
            insert(ComputeGrant)
            .values(
                id=uuid.uuid4(),
                team_id=team_id,
                source=GrantSource.PLAN_PERIOD.value,
                period_start=month,
                expires_at=month_end(month),
                credits_total=credits,
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
        expires_at: datetime | None = None,
        reason: str | None = None,
    ) -> ComputeGrant:
        if not math.isfinite(credits_total) or credits_total <= 0:
            raise ValueError("credits must be finite and positive")
        return await self._add(
            ComputeGrant(
                team_id=team_id,
                source=source.value,
                expires_at=expires_at,
                reason=reason,
                credits_total=credits_total,
            )
        )

    async def _add(self, pack: ComputeGrant) -> ComputeGrant:
        pack.credits_used = 0.0
        self._session.add(pack)
        await self._session.flush()
        return pack
