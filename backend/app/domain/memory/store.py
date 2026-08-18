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
from app.domain.memory.keywords import is_relevant, match_content, query_terms
from app.domain.memory.models import MemoryEntry, MemoryScope

# How many facts one pool contributes to the prompt, and how many characters
# all pools together may spend there. A count cap alone is not a budget: 50
# one-line facts and 50 paragraph-long ones differ by an order of magnitude in
# prompt weight, and the second kind is what pushes everything else out. The
# character budget is set generously on purpose, against measured data rather
# than a guess: this project's 63 facts (median 225 chars) inject at ~13k
# chars, so the budget bites only a pool that has genuinely bloated and today
# changes nothing about what gets in. And never silently either way: whatever
# cap dropped it, it comes back as ``RecallResult.omitted`` for the prompt to
# state out loud.
MEMORY_INJECTION_LIMIT = 50
MEMORY_INJECTION_CHAR_BUDGET = 20000


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
        """Return up to `limit` memory facts for prompt injection, oldest first.
        DB backend: most recent raw facts. OpenViking backend: L0 abstract
        lines (summaries only — the layered-memory contract)."""
        ...

    async def count(self, scope: MemoryScope, scope_id: str) -> int:
        """How many facts the pool actually holds — including the ones `recall`
        left out. Injection compares the two so truncation can be stated."""
        ...

    async def remember(self, scope: MemoryScope, scope_id: str, content: str) -> None:
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


async def recall_pools(
    store: MemoryStore,
    pools: list[tuple[MemoryScope, str]],
    *,
    limit: int = MEMORY_INJECTION_LIMIT,
    char_budget: int = MEMORY_INJECTION_CHAR_BUDGET,
) -> RecallResult:
    """Recall several pools into one injection block, counting what was cut.

    Silent truncation is the failure this exists to prevent: the writer thinks
    the fact was stored (it was), the reader never learns something was held
    back, and both conclude memory is empty. Pools are read newest-first under
    a shared character budget — an earlier pool's leftover rolls forward — and
    everything the caps dropped comes back as ``omitted`` for the prompt to say
    out loud.
    """
    facts: list[str] = []
    stored = 0
    budget = char_budget
    pools_left = len(pools)
    for scope, scope_id in pools:
        picked = await store.recall(scope, scope_id, limit)
        stored += await store.count(scope, scope_id)
        share = budget // pools_left if pools_left else budget
        kept: list[str] = []
        used = 0
        for fact in reversed(picked):  # newest first — the oldest gets dropped
            cost = len(fact) + 2
            if kept and used + cost > share:
                break
            used += cost
            kept.append(fact)
        kept.reverse()  # back to oldest-first, the injection order
        facts.extend(kept)
        budget -= used
        pools_left -= 1
    return RecallResult(facts=facts, omitted=max(0, stored - len(facts)))


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

    async def remember(self, scope: MemoryScope, scope_id: str, content: str) -> None:
        self._session.add(MemoryEntry(scope=scope, scope_id=scope_id, content=content))
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
