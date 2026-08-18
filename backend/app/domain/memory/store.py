"""Memory store abstraction.

Two backends behind one Protocol (settings.memory_backend):
- DbMemoryStore  — flat memory_entries projection in PG (Phase 0 default).
- OpenVikingMemoryStore — embedded OpenViking layered memory (viking:// FS,
  L0 abstracts injected, full text behind semantic search; spec §8.4/§15 Q9).

The block tree remains the source of truth; memory is a fast-recall projection.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.memory.keywords import (
    INJECTION_MAX_TERMS,
    is_relevant,
    match_content,
    query_terms,
)
from app.domain.memory.models import MemoryEntry, MemoryLayer, MemoryScope

# Two budgets, because the two layers are paid for differently.
#
# `core` is what the agent must know to be itself — it is injected in full,
# every turn, and never competes with anything for a seat. That is only safe
# while it stays small, so it gets a budget an order of magnitude tighter than
# the other one: the cap is the thing that keeps "always injected" affordable,
# and blowing through it is a signal that core has stopped being core.
#
# `fact` is everything learned. A pool of these outgrows any prompt eventually
# — this project's own is 155 facts / ~42k chars — so the budget's job is not
# to hold all of it but to spend the room on the facts this turn is about; the
# rest stays one `cheese recall` away.
#
# Neither cap is silent: whatever they dropped comes back on ``RecallResult``
# for the prompt to state out loud.
MEMORY_CORE_CHAR_BUDGET = 4000
MEMORY_INJECTION_CHAR_BUDGET = 20000

# How many non-core facts one pool ranks. Ranking happens in Python over the
# fetched rows, so this bounds the work; pools are ~150 facts today, so it does
# not bite. Anything past it is still counted as omitted, never silently gone.
MEMORY_RANK_CANDIDATES = 500


def live_entries() -> ColumnElement[bool]:
    """The one clause every read of ``memory_entries`` must carry.

    记忆整理 retires facts instead of deleting them (see MemoryDream), so the
    table holds rows that are deliberately no longer part of the memory. A read
    that forgets this filter does not fail — it quietly reinstates every fact
    芝士 ever decided was wrong, which is worse than never having organized.
    """
    return MemoryEntry.retired_at.is_(None)


class MemoryStore(Protocol):
    async def recall(
        self, scope: MemoryScope, scope_id: str, limit: int = 50
    ) -> list[str]:
        """Return up to `limit` memory facts, newest ones, oldest first.
        DB backend: raw facts of every layer. OpenViking backend: L0 abstract
        lines (summaries only — the layered-memory contract)."""
        ...

    async def recall_core(self, scope: MemoryScope, scope_id: str) -> list[str]:
        """The pool's core layer, in full, oldest first. Prompt injection puts
        all of it in every turn, so what is in here is a curation decision, not
        a retrieval one."""
        ...

    async def rank_facts(
        self,
        scope: MemoryScope,
        scope_id: str,
        query: str,
        limit: int = MEMORY_RANK_CANDIDATES,
    ) -> list[tuple[float, str]]:
        """Non-core facts as ``(score, content)``, best match first.

        ``query`` is this turn's context. An empty query (or one with nothing
        to match on) scores everything 0.0 and the order degrades to newest
        first — the caller still gets a full budget's worth, it just has no
        signal to pick with."""
        ...

    async def count(self, scope: MemoryScope, scope_id: str) -> int:
        """How many facts the pool actually holds — including the ones a turn
        did not retrieve. Injection compares the two so what is missing from
        this turn can be stated."""
        ...

    async def remember(
        self,
        scope: MemoryScope,
        scope_id: str,
        content: str,
        *,
        layer: MemoryLayer = MemoryLayer.fact,
    ) -> None:
        """Persist a new memory fact."""
        ...

    async def search(
        self, scope: MemoryScope, scope_id: str, query: str, limit: int = 8
    ) -> list[Any]:
        """Query memories on demand (芝士's recall tool). Backends return
        objects with an ``as_dict()`` method."""
        ...


@dataclass(frozen=True)
class RecallResult:
    """What injection got, and what it did not get."""

    facts: list[str]
    omitted: int
    # How many leading entries of ``facts`` are the core layer. The prompt says
    # so, because "always here" and "here because you happened to ask about it"
    # are different promises and the reader has to be able to tell them apart.
    core_count: int = 0
    # Core that did not fit its own budget. Distinct from ``omitted`` on
    # purpose: a missing fact is normal, a missing *core* fact means the layer
    # that is supposed to be unconditional has stopped being unconditional.
    core_omitted: int = 0


async def recall_pools(
    store: MemoryStore,
    pools: list[tuple[MemoryScope, str]],
    *,
    query: str = "",
    core_char_budget: int = MEMORY_CORE_CHAR_BUDGET,
    char_budget: int = MEMORY_INJECTION_CHAR_BUDGET,
) -> RecallResult:
    """Recall several pools into one injection block: core first, then whatever
    this turn is about.

    Two failures are being avoided at once. The first is silent truncation —
    the writer thinks the fact was stored (it was), the reader never learns
    something was held back, and both conclude memory is empty; so everything
    the budgets dropped comes back as ``omitted``. The second is a pool that
    outgrew the prompt, where taking "the newest N" means the seats go to
    whatever was written last, which has nothing to do with what the turn
    needs. Core is exempt from that competition by construction; the rest is
    ranked against ``query`` and fills what room is left, best match first.
    """
    core: list[str] = []
    core_used = 0
    core_omitted = 0
    ranked: list[tuple[float, str]] = []
    stored = 0
    for scope, scope_id in pools:
        stored += await store.count(scope, scope_id)
        kept: list[str] = []
        for fact in reversed(await store.recall_core(scope, scope_id)):
            cost = len(fact) + 2
            # `core or kept` — one core fact always gets in, even alone over
            # budget. An empty core layer is indistinguishable from having no
            # identity at all, which is the worse failure of the two.
            if (core or kept) and core_used + cost > core_char_budget:
                core_omitted += 1
                continue
            core_used += cost
            kept.append(fact)
        kept.reverse()  # back to oldest-first, the injection order
        core.extend(kept)
        ranked.extend(await store.rank_facts(scope, scope_id, query))
    # Stable sort over per-pool lists that are already best-first: relevance
    # decides, and among equally-relevant facts the agent's own pool (listed
    # first by the caller) and then the newer fact keep their order.
    ranked.sort(key=lambda pair: -pair[0])
    facts: list[str] = []
    used = 0
    for _, fact in ranked:
        cost = len(fact) + 2
        if facts and used + cost > char_budget:
            break
        used += cost
        facts.append(fact)
    return RecallResult(
        facts=core + facts,
        omitted=max(0, stored - len(core) - len(facts)),
        core_count=len(core),
        core_omitted=core_omitted,
    )


# Keyword matching happens in SQL, ranking in Python, so the fetch has to be
# wider than what is returned or a highly-relevant older fact would be cut by
# recency before it is ever scored. It is still a cap: in a pool with more than
# _MIN_CANDIDATES keyword matches, the oldest of them never get ranked. Today's
# pools are ~60 facts, so nothing comes close; revisit alongside real ranking
# (the openviking backend) rather than by raising this number forever.
_CANDIDATE_FACTOR = 20
_MIN_CANDIDATES = 200


def _like(term: str) -> str:
    """A LIKE pattern matching ``term`` literally — `%` and `_` inside a
    keyword are characters, not wildcards."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class DbMemoryStore:
    """MemoryStore backed by the memory_entries table."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def recall(
        self, scope: MemoryScope, scope_id: str, limit: int = 50
    ) -> list[str]:
        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.scope == scope,
                MemoryEntry.scope_id == scope_id,
                live_entries(),
            )
            .order_by(MemoryEntry.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [r.content for r in reversed(rows)]

    async def recall_core(self, scope: MemoryScope, scope_id: str) -> list[str]:
        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.scope == scope,
                MemoryEntry.scope_id == scope_id,
                MemoryEntry.layer == MemoryLayer.core,
                live_entries(),
            )
            .order_by(MemoryEntry.created_at)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [r.content for r in rows]

    async def rank_facts(
        self,
        scope: MemoryScope,
        scope_id: str,
        query: str,
        limit: int = MEMORY_RANK_CANDIDATES,
    ) -> list[tuple[float, str]]:
        """Score every non-core fact against the turn's context.

        Matching is the same keyword coverage `search` ranks with, run over the
        whole (non-core) pool rather than a SQL-prefiltered subset: with no
        keyword to match, `search` would return nothing and injection must
        still fill its budget. So the filter is the budget, not the query — a
        turn with no signal degrades to newest-first, which is where this
        started, and a turn with signal spends its room on the facts that
        actually mention what it is about.
        """
        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.scope == scope,
                MemoryEntry.scope_id == scope_id,
                MemoryEntry.layer == MemoryLayer.fact,
                live_entries(),
            )
            .order_by(MemoryEntry.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        terms = query_terms(query, max_terms=INJECTION_MAX_TERMS)
        if not terms:
            return [(0.0, r.content) for r in rows]
        scored = [(match_content(terms, r.content)[0], r.content) for r in rows]
        # Stable over a recency-ordered fetch: coverage decides, ties keep the
        # newest first.
        scored.sort(key=lambda pair: -pair[0])
        return scored

    async def remember(
        self,
        scope: MemoryScope,
        scope_id: str,
        content: str,
        *,
        layer: MemoryLayer = MemoryLayer.fact,
    ) -> None:
        self._session.add(
            MemoryEntry(scope=scope, scope_id=scope_id, content=content, layer=layer)
        )
        await self._session.flush()

    async def count(self, scope: MemoryScope, scope_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(MemoryEntry)
            .where(
                MemoryEntry.scope == scope,
                MemoryEntry.scope_id == scope_id,
                live_entries(),
            )
        )
        return int(await self._session.scalar(stmt) or 0)

    async def search(
        self, scope: MemoryScope, scope_id: str, query: str, limit: int = 8
    ) -> list[Any]:
        """Keyword search — the flat backend has no semantic index.

        The query is cut into keywords (latin words, CJK bigrams and phrases),
        every fact matching *any* of them is fetched, and each is ranked by how
        much of the query it covers. Still a structural filter over stored
        facts, not NL interpretation (规则4) — which is why nothing that
        surfaces this calls it 语义搜索. A recall named better than it works is
        worse than a weak one: one empty result and the caller concludes the
        memory does not exist, then walks into the very thing it warned about.
        """
        from app.domain.memory.openviking_store import MemoryHit

        terms = query_terms(query)
        # No usable keyword (punctuation only, or nothing but stopwords): the
        # whole string is all the signal there is.
        matches = (
            or_(
                *(
                    MemoryEntry.content.ilike(_like(term), escape="\\")
                    for term, _ in terms
                )
            )
            if terms
            else MemoryEntry.content.ilike(_like(query), escape="\\")
        )
        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.scope == scope,
                MemoryEntry.scope_id == scope_id,
                matches,
                live_entries(),
            )
            .order_by(MemoryEntry.created_at.desc())
            .limit(max(limit * _CANDIDATE_FACTOR, _MIN_CANDIDATES))
        )
        rows = list((await self._session.scalars(stmt)).all())
        scored = []
        for row in rows:
            if not terms:  # whole-string fallback: matching at all is the hit
                scored.append((1.0, row))
                continue
            coverage, strongest = match_content(terms, row.content)
            if is_relevant(coverage, strongest):
                scored.append((coverage, row))
        # Stable sort over a recency-ordered fetch: coverage decides, and among
        # equally-relevant facts the newest still wins.
        scored.sort(key=lambda pair: -pair[0])
        return [
            MemoryHit(uri=f"db://memory/{r.id}", abstract=r.content, score=round(s, 3))
            for s, r in scored[:limit]
        ]


def memory_store(session: AsyncSession) -> MemoryStore:
    """Backend selector (settings.memory_backend: "db" | "openviking").

    The OpenViking store is process-global and ignores the DB session; the
    parameter keeps one call shape for both backends."""
    if settings.memory_backend == "openviking":
        # Lazy import: the openviking package pulls heavy deps (litellm etc.)
        # that "db" deployments never need to load.
        from app.domain.memory.openviking_store import OpenVikingMemoryStore

        return OpenVikingMemoryStore()
    return DbMemoryStore(session)
