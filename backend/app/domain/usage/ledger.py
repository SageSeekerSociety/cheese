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

**How this file is divided.** ``Ledger`` is the whole account, and its two
halves live one each side of one line: ``ledger_queries.LedgerQueries`` holds
what only reads — which packs exist, how full each window is, whether a call may
run — and this module holds what writes: the charges, the deduction, the plan
pack and the grants. ``Ledger`` inherits the reading half, so a call site keeps
calling both halves as methods of one object and never has to know which file a
method is in.

The rules both halves share live beside them, each where it belongs rather than
in this file: a plan's time windows (``windows``), the rates a call is priced
at (``rates``), who pays and what their plan allows (``payers``), and what a
payer may still spend (``balance``). Three of the four decide without a session
and without a row — ``windows``, ``rates`` and ``balance`` — and none of them
charges; ``payers`` is the fourth and the one exception, because working out
who pays needs the team's plan read back, and a person without a personal team
gets one made on the way (``team_terms``, ``payer_for_person``).

Every name a caller takes from this module is still answered from here: the
re-exports below are the public path, not a convenience, because callers and
tests all along have written ``from app.domain.usage.ledger import ...``.

Two things about that face are worth saying rather than letting someone find
out. Most of the private helpers that used to sit at this module's top level
(``_live``, ``_order``, ``_balance``, ``_hours`` and the rest) moved with the
code that reads them, so this path no longer answers them; nothing in the repo
takes them from here, and ``_TZ`` is re-exported below because ``usage.report``
does. ``_applies`` and ``_spendable`` are here because this half calls them, not
for anyone else. And a re-export is not a second definition: a patch put on one
of these names here would change what this module's own name means and not what
the module that reads it sees, which is where the patch has to go.
"""

import math
import uuid
from datetime import UTC, datetime

from sqlalchemy import case, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage.balance import (  # noqa: F401
    Balance,
    Refusal,
    _applies,
    _spendable,
)
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.credits import spend_to_credits
from app.domain.usage.ledger_queries import LedgerQueries
from app.domain.usage.models import ComputeGrant, GrantSource, PlanWindowUse
from app.domain.usage.payers import (  # noqa: F401
    Payer,
    Terms,
    payer_for_person,
    payer_for_project,
    payers_for_projects,
    project_with_payer,
    team_terms,
    terms_of,
)
from app.domain.usage.rates import RateRow, Rates  # noqa: F401
from app.domain.usage.repositories import UsageRepository
from app.domain.usage.windows import (  # noqa: F401
    _TZ,
    Window,
    WindowUse,
    _window_use,
    month_end,
    month_of,
    week_of,
)


class Ledger(LedgerQueries):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        calls: int = 1,
        attribute_to_work: bool = True,
    ) -> float:
        """Write one usage row, naming the team that paid and the credits it
        cost, and charge those credits: the row and the deduction cannot come
        apart. Every usage row is written here, an unmetered one (work done,
        tokens unknown) too, so each names the team it belongs to. Returns the
        credits deducted."""
        await UsageRepository(self._session).add(
            project_id=payer.project_id,
            conversation_id=topic_id,
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
            credit_owner_handle=await CreditBudgets(self._session).owner_of(
                turn_id, payer.project_id
            )
            if attribute_to_work
            else None,
            team_id=payer.team_id,
            credits=credits,
            metered=metered,
            calls=calls,
        )
        return await self.charge(payer, credits)

    async def record_project_call(
        self,
        project_id: uuid.UUID,
        *,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        topic_id: uuid.UUID | None = None,
    ) -> float:
        """Write and charge one model call the platform made for a project on
        its own (naming a task, moving the project's old memories, reading why
        its environment failed). The gateway priced it in the response, and
        the project's team pays it like a room turn's spend, under ``kind`` so
        the call stays apart in the records. Returns the credits deducted."""
        return await self.record(
            await payer_for_project(self._session, project_id),
            credits=spend_to_credits(cost_usd),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            route="gateway",
            kind=kind,
            topic_id=topic_id,
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
        # deferred-import: tests replace this name on app.domain.project.services
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
