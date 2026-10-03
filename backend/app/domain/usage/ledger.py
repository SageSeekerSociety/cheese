"""Credits: who pays for a model call, whether it may run, and what it costs
(#2397).

Credits belong only to teams, never to a person inside one. A person's own
credits are packs on their personal team. Where a call happens decides which
team pays: a team's project, that team; a call outside any project or in the
person's own project, their personal team — including when someone else in
that project's room summons 芝士.

Every team is on a plan (``Plan``), and a plan bills one of two ways.

A monthly plan issues a pack for the month the first time a call of that month
is charged; until then the balance counts it as already there, so reading a
balance never writes. A charge drains the packs that apply in this order:

1. what a task earmarked for this project;
2. the paying team's plan pack for the month;
3. credits bought or granted by an administrator, what lapses first first.

A windowed plan issues no pack. It lets a team spend up to a cap inside each
of its time windows: an hours window starts at the team's first call and
resets that many hours later, a week or month window resets every Monday or
every first of the month (Asia/Shanghai). A charge drains a task's earmark
first, which never fills a window; the rest fills every window. Once any window
is full, the rest drains bought or granted credits instead, and with none the
call waits for the window to reset.

The last call may overdraw; it was admitted with credits left, and what it
spent is spent either way. An unlimited plan refuses nothing, issues nothing
and draws on nothing; its usage is still recorded.
"""

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import NoticeText, say
from app.domain.usage.credits import spend_to_credits
from app.domain.usage.models import ComputeGrant, GrantSource, Plan, PlanWindowUse
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


def week_of(moment: datetime) -> datetime:
    """The Monday midnight that starts the week ``moment`` falls in."""
    local = moment.astimezone(_TZ)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=_TZ)


@dataclass(frozen=True)
class Window:
    """One of a plan's time windows: at most ``credits`` inside it. An hours
    window starts at a team's first call; a ``calendar`` one is a week or a
    month."""

    credits: float
    hours: float | None = None
    calendar: str | None = None

    @classmethod
    def of(cls, raw: Mapping) -> "Window":
        if raw.get("calendar") is not None:
            return cls(credits=float(raw["credits"]), calendar=str(raw["calendar"]))
        return cls(credits=float(raw["credits"]), hours=float(raw["hours"]))

    @property
    def key(self) -> str:
        """The window's row in ``plan_window_use``."""
        return self.calendar or f"{self.hours:g}h"

    def fresh_since(self, now: datetime) -> datetime:
        """A round that started before this has run out."""
        if self.calendar == "week":
            return week_of(now)
        if self.calendar == "month":
            return datetime.combine(month_of(now), datetime.min.time(), tzinfo=_TZ)
        assert self.hours is not None
        return now - timedelta(hours=self.hours)

    def start(self, now: datetime) -> datetime:
        """When a round that starts with a call at ``now`` began."""
        return now if self.calendar is None else self.fresh_since(now)

    def resets_at(self, started_at: datetime) -> datetime:
        if self.calendar == "week":
            return started_at + timedelta(days=7)
        if self.calendar == "month":
            return month_end(month_of(started_at))
        assert self.hours is not None
        return started_at + timedelta(hours=self.hours)


@dataclass(frozen=True)
class WindowUse:
    """How full one window is now. ``resets_at`` is None for an hours window no
    call has started yet."""

    window: Window
    credits_used: float
    resets_at: datetime | None

    @property
    def full(self) -> bool:
        return self.credits_used >= self.window.credits

    @property
    def room(self) -> float:
        return max(0.0, self.window.credits - self.credits_used)


def _window_use(window: Window, row: PlanWindowUse | None, now: datetime) -> WindowUse:
    if row is not None and row.started_at >= window.fresh_since(now):
        return WindowUse(window, row.credits_used, window.resets_at(row.started_at))
    if window.calendar is None:
        return WindowUse(window, 0.0, None)
    return WindowUse(window, 0.0, window.resets_at(window.start(now)))


#: One model's row in the gateway's rate table: input, output, cache read,
#: five-minute cache write and, when the gateway names it, one-hour cache write.
RateRow = tuple[float, float, float, float, float | None]


@dataclass(frozen=True)
class Rates:
    """USD per token for the model a call will use: fresh prompt tokens, output
    tokens, prompt tokens read from the provider's cache, prompt tokens written
    to its five-minute cache, and to its one-hour cache (the five-minute rate
    when the table names none)."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    cache_write_1h: float | None = None

    def cost_usd(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
    ) -> float:
        """What a call costs at these rates, billed the way the gateway bills
        it: ``input_tokens`` counts every prompt token, cached ones included,
        and the cached shares are billed at their own rates.
        ``cache_write_1h_tokens`` is the share of ``cache_write_tokens`` that
        went to the one-hour cache."""
        fresh = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        hour = min(cache_write_1h_tokens, cache_write_tokens)
        hour_rate = (
            self.cache_write if self.cache_write_1h is None else self.cache_write_1h
        )
        return (
            fresh * self.input
            + cache_read_tokens * self.cache_read
            + (cache_write_tokens - hour) * self.cache_write
            + hour * hour_rate
            + output_tokens * self.output
        )

    @classmethod
    def of(cls, model: str, table: Mapping[str, RateRow] | None) -> "Rates | None":
        """``model``'s rates from the gateway's rate table
        (``feature_stats.pricing.model_rates``); None when a call to it cannot
        be charged."""
        if not table or model not in table:
            return None
        return cls(*table[model])


@dataclass(frozen=True)
class Terms:
    """What a team's plan says about spending."""

    key: str
    unlimited: bool = False
    credits_per_period: float | None = None
    # A windowed plan's windows; a monthly plan has none.
    windows: tuple[Window, ...] = ()
    # The model tiers the plan allows; None is every tier.
    model_tiers: frozenset[str] | None = None

    @classmethod
    def of(cls, plan: Plan) -> "Terms":
        return cls(
            key=plan.key,
            unlimited=plan.unlimited,
            credits_per_period=plan.credits_per_period,
            windows=tuple(Window.of(w) for w in plan.windows or []),
            model_tiers=(
                None if plan.model_tiers is None else frozenset(plan.model_tiers)
            ),
        )

    @property
    def issues(self) -> float:
        """The pack the plan issues each month; 0 when it issues none."""
        if self.unlimited or self.windowed or not self.credits_per_period:
            return 0.0
        return float(self.credits_per_period)

    @property
    def windowed(self) -> bool:
        return not self.unlimited and bool(self.windows)


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
    return f"{hours:g}"


def _day(at: datetime) -> dict:
    local = at.astimezone(_TZ)
    return {"month": local.month, "day": local.day, "time": local.strftime("%H:%M")}


def _order(pack: ComputeGrant) -> tuple:
    # Within a rank, what lapses first is spent first.
    return (_rank(pack), pack.expires_at or _FAR, pack.created_at, pack.id)


def _window_refusal(use: WindowUse) -> Refusal:
    at = use.resets_at
    assert at is not None
    if use.window.calendar == "week":
        return Refusal(say("creditsWeekFull", **_day(at)), at)
    if use.window.calendar == "month":
        local = at.astimezone(_TZ)
        return Refusal(say("creditsMonthFull", month=local.month, day=local.day), at)
    assert use.window.hours is not None
    return Refusal(
        say("creditsWindowFull", hours=_hours(use.window.hours), **_day(at)), at
    )


@dataclass(frozen=True)
class Balance:
    """What a payer may still spend. For a windowed plan, what its fullest
    window still allows counts alongside the packs, as if it were one."""

    unlimited: bool
    credits_total: float
    credits_used: float
    # When the payer's plan next issues a pack; None when it issues none.
    resets_at: datetime | None
    packs: tuple[ComputeGrant, ...] = ()
    # A windowed plan's windows, each as full as it is now.
    windows: tuple[WindowUse, ...] = ()

    @property
    def credits_remaining(self) -> float:
        return self.credits_total - self.credits_used

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.credits_remaining <= 0

    def packs_remaining(self) -> float:
        """What is left of every pack besides the plan's own."""
        return sum(
            p.credits_total - p.credits_used
            for p in self.packs
            if p.source != GrantSource.PLAN_PERIOD
        )

    def refusal(self) -> Refusal:
        full = [w for w in self.windows if w.full]
        if full:
            return _window_refusal(max(full, key=lambda w: w.resets_at or _FAR))
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
    drains them. A windowed plan spends no plan pack, even one issued for this
    month before the plan stopped issuing them."""
    now = datetime.now(UTC)
    return sorted(
        (
            p
            for p in candidates
            if _applies(p, payer)
            and _live(p, now)
            and not (payer.terms.windowed and p.source == GrantSource.PLAN_PERIOD)
        ),
        key=_order,
    )


def _issued_this_month(candidates: Iterable[ComputeGrant], payer: Payer) -> bool:
    month = month_of(datetime.now(UTC))
    return any(
        p.source == GrantSource.PLAN_PERIOD
        and p.team_id == payer.team_id
        and p.period_start == month
        for p in candidates
    )


def _balance(
    candidates: list[ComputeGrant],
    payer: Payer,
    rows: Mapping[tuple[int, str], PlanWindowUse],
) -> Balance:
    packs = _spendable(candidates, payer)
    # The month's plan pack counts before its first charge writes it.
    unissued = 0.0
    if payer.terms.issues and not _issued_this_month(candidates, payer):
        unissued = payer.terms.issues
    resets_at = None
    if payer.terms.issues:
        resets_at = month_end(month_of(datetime.now(UTC)))
    windows: tuple[WindowUse, ...] = ()
    room = 0.0
    if payer.terms.windowed:
        now = datetime.now(UTC)
        windows = tuple(
            _window_use(w, rows.get((payer.team_id, w.key)), now)
            for w in payer.terms.windows
        )
        room = min(w.room for w in windows)
    return Balance(
        unlimited=payer.terms.unlimited,
        credits_total=sum(p.credits_total for p in packs) + unissued + room,
        credits_used=sum(p.credits_used for p in packs),
        resets_at=resets_at,
        packs=tuple(packs),
        windows=windows,
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

    async def _window_rows(
        self, payers: Iterable[Payer]
    ) -> dict[tuple[int, str], PlanWindowUse]:
        """The window rows of every windowed plan among ``payers``' teams."""
        teams = {p.team_id for p in payers if p.terms.windowed}
        if not teams:
            return {}
        rows = await self._session.execute(
            select(PlanWindowUse).where(PlanWindowUse.team_id.in_(teams))
        )
        return {(r.team_id, r.window): r for r in rows.scalars()}

    async def window_uses(
        self, payers: Iterable[Payer]
    ) -> dict[int, tuple[WindowUse, ...]]:
        """Team → how full each of its plan's windows is now; only teams on a
        windowed plan."""
        payers = list(payers)
        rows = await self._window_rows(payers)
        now = datetime.now(UTC)
        return {
            p.team_id: tuple(
                _window_use(w, rows.get((p.team_id, w.key)), now)
                for w in p.terms.windows
            )
            for p in payers
            if p.terms.windowed
        }

    async def balance(self, payer: Payer) -> Balance:
        return _balance(
            await self._candidates([payer]), payer, await self._window_rows([payer])
        )

    async def admit(self, payer: Payer) -> Refusal | None:
        """Whether a call ``payer`` would pay for may run now; None if it may.

        A windowed plan admits while every window has room. Once one is full,
        only an earmark or bought or granted credits may be spent; with none,
        the call waits for the window to reset."""
        if payer.terms.unlimited:
            return None
        balance = await self.balance(payer)
        if payer.terms.windowed:
            if not any(w.full for w in balance.windows):
                return None
            if balance.packs_remaining() > 0:
                return None
            return balance.refusal()
        if balance.exhausted:
            return balance.refusal()
        return None

    async def balances(self, payers: Mapping[uuid.UUID, Payer]) -> dict:
        """``balance`` for many payers at once, in two queries."""
        candidates = await self._candidates(payers.values())
        rows = await self._window_rows(payers.values())
        return {key: _balance(candidates, payer, rows) for key, payer in payers.items()}

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
        """Charge ``credits`` to ``payer`` in the order its plan sets. On a
        monthly plan, what no pack has room for lands on the last one, or, with
        none live, on the newest pack the payer ever had, so recorded
        consumption stays truthful. Returns the credits charged."""
        if credits <= 0 or payer.terms.unlimited:
            return 0.0
        if payer.terms.windowed:
            return await self._charge_windowed(payer, credits)
        if payer.terms.issues:
            await self._plan_pack(payer.team_id, payer.terms.issues)
        candidates = await self._candidates([payer], lock=True)
        packs = _spendable(candidates, payer)
        if not packs:
            everything = [p for p in candidates if _applies(p, payer)]
            if not everything:
                return 0.0
            packs = [max(everything, key=lambda p: (p.created_at, p.id))]
        left = await self._drain(packs, credits)
        if left > 0:
            await self._deduct(packs[-1].id, left)
        return credits

    async def _charge_windowed(self, payer: Payer, credits: float) -> float:
        """A windowed plan's charge: the earmark first, then every window while
        none is full, then bought or granted credits. What those have no room
        for fills the windows anyway; the call was admitted on what remained."""
        candidates = await self._candidates([payer], lock=True)
        packs = _spendable(candidates, payer)
        left = await self._drain(
            [p for p in packs if p.project_id is not None], credits
        )
        if left <= 0:
            return credits
        rows = await self._window_rows([payer])
        now = datetime.now(UTC)
        full = any(
            _window_use(w, rows.get((payer.team_id, w.key)), now).full
            for w in payer.terms.windows
        )
        if full:
            left = await self._drain([p for p in packs if p.project_id is None], left)
        if left > 0:
            await self._fill_windows(payer, left, now)
        return credits

    async def _drain(self, packs: list[ComputeGrant], credits: float) -> float:
        """Deduct ``credits`` from ``packs`` in order, as far as they have room.
        Returns what is left over."""
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
        return max(0.0, left)

    async def _fill_windows(self, payer: Payer, credits: float, now: datetime) -> None:
        """Add ``credits`` to every window of ``payer``'s plan. A round that has
        run out starts again at this call; two charges at once both count."""
        for window in payer.terms.windows:
            stale = PlanWindowUse.started_at < window.fresh_since(now)
            start = window.start(now)
            await self._session.execute(
                insert(PlanWindowUse)
                .values(
                    team_id=payer.team_id,
                    window=window.key,
                    started_at=start,
                    credits_used=credits,
                )
                .on_conflict_do_update(
                    index_elements=["team_id", "window"],
                    set_={
                        "started_at": case(
                            (stale, start), else_=PlanWindowUse.started_at
                        ),
                        "credits_used": case(
                            (stale, credits),
                            else_=PlanWindowUse.credits_used + credits,
                        ),
                    },
                )
            )

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
        return await self.record(
            payer,
            credits=spend_to_credits(cost),
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            kind=kind,
            route="gateway",
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
        return await self.record(
            payer,
            credits=spend_to_credits(cost_usd),
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
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
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
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_write_1h_tokens=cache_write_1h_tokens,
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

    async def record_platform(
        self,
        *,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        project_id: uuid.UUID | None = None,
        topic_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
        metered: bool = True,
        route: str = "gateway",
    ) -> None:
        """Write the usage row of a call the platform made on its own (naming a
        room, sorting old memories, consolidating a project's memory). The
        platform pays: the row names no team or person and costs no credits,
        so no balance, time window or usage page counts it (#2233). Work done
        inside a project keeps the project, so its own records can still find
        it."""
        await UsageRepository(self._session).add(
            project_id=project_id,
            topic_id=topic_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            kind=kind,
            metered=metered,
            route=route,
            turn_id=turn_id,
        )

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
