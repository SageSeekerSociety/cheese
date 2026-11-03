"""The ledger's reading half: which packs exist and what they still allow.

The ledger (``ledger.Ledger``) is both halves of the same account, and the seam
between them is one line: only SELECT here, everything that adds or flushes or
assigns in ``ledger.py``. Answering "does this method change anything" used to
mean reading the body of every one of them; after the split the module a method
lives in answers it.

Nothing here changes a row. The one write-shaped thing, ``balance`` counting a
month's plan pack the month has not written yet, writes nothing either — it
counts it as already there so reading a balance never writes
(``ledger.Ledger._plan_pack`` is where it really lands, on the first charge).

``Ledger`` inherits this class, so every call site keeps calling these as
methods of ``Ledger`` — the session is still put in ``self._session`` by
`Ledger.__init__``. The direction is forced: a charge reads the packs it drains,
never the other way round, and ``ledger.py`` importing this one is that edge.
"""

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.usage.balance import Balance, Refusal, _balance, _live
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.models import ComputeGrant, PlanWindowUse
from app.domain.usage.payers import Payer
from app.domain.usage.windows import WindowUse, _window_use


class LedgerQueries:
    """Reads over the packs and window rows a payer draws on.

    No ``__init__``: ``Ledger`` puts the session in ``self._session``, which is
    what lets every call site keep calling these as methods of ``Ledger``.
    """

    _session: AsyncSession

    async def _candidates(
        self, payers: Iterable[Payer], *, lock: bool = False
    ) -> list[ComputeGrant]:
        """Every pack any of ``payers`` might draw on, expired ones included;
        ``_applies`` and ``_live`` narrow them per payer."""
        payers = list(payers)
        projects = {p.project_id for p in payers if p.project_id is not None}
        teams = {p.team_id for p in payers}
        # Admitting a model call reads these packs, then reads them again for
        # the budget its gateway key carries (`routes/llm_proxy.py`); the
        # second read is answered here, before its select is put together.
        can_cache = not lock and isinstance(self._session, AsyncSession)
        key = ("grant_candidates", frozenset(teams), frozenset(projects))
        if can_cache:
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(self._session, key)
            if found:
                return list(cached)
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
        grants = list((await self._session.execute(stmt)).scalars())
        if can_cache:
            remember(
                self._session, key, list(grants), tables=frozenset({"compute_grants"})
            )
        return grants

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
        key = payer.project_id or uuid.UUID(int=0)
        return (await self.admit_many({key: payer}))[key]

    async def admit_many(
        self, payers: Mapping[uuid.UUID, Payer]
    ) -> dict[uuid.UUID, Refusal | None]:
        """``admit`` for many payers at once: budgets, packs and windows are
        each read once for the whole batch."""
        budgets = await CreditBudgets(self._session).project_refusals(
            [p.project_id for p in payers.values() if p.project_id is not None]
        )
        paying = {
            key: p
            for key, p in payers.items()
            if p.project_id not in budgets and not p.terms.unlimited
        }
        balances = await self.balances(paying) if paying else {}
        return {
            key: _verdict(payer, budgets, balances.get(key))
            for key, payer in payers.items()
        }

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


def _verdict(
    payer: Payer, budgets: Mapping[uuid.UUID, Refusal], balance: Balance | None
) -> Refusal | None:
    if payer.project_id is not None and payer.project_id in budgets:
        return budgets[payer.project_id]
    if balance is None:
        return None
    if payer.terms.windowed:
        if not any(w.full for w in balance.windows) or balance.packs_remaining() > 0:
            return None
        return balance.refusal()
    return balance.refusal() if balance.exhausted else None
