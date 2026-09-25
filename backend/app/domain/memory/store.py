"""Memory store — the flat ``memory_entries`` projection in PG.

The block tree remains the source of truth; memory is a fast-recall projection.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.keywords import is_relevant, match_content, query_terms
from app.domain.memory.models import (
    MemoryEntry,
    MemoryLayer,
    MemoryScope,
    user_scope_about,
)

# Only `core` is injected. It is what the agent must know to be itself, it is
# in every turn by definition, and it is small enough that being in every turn
# is affordable — blowing through this cap is the signal that something in
# there stopped being core.
#
# `fact` is everything learned, and it is NOT injected: it is retrieved by
# `recall`, when the turn asks. Injection used to spend 20000 characters per
# turn guessing which facts this turn was about, and the guess was paid for
# whether or not it was right — while `recall` sat there unused, because a
# prompt that already looks full is a prompt nobody searches. What injection
# still owes is the one line saying the pool is not empty; the rest is a query
# away.
MEMORY_CORE_CHAR_BUDGET = 4000


def live_entries() -> ColumnElement[bool]:
    """The one clause every read of ``memory_entries`` must carry.

    记忆整理 retires facts instead of deleting them (see MemoryDream), so the
    table holds rows that are deliberately no longer part of the memory. A read
    that forgets this filter does not fail — it quietly reinstates every fact
    芝士 ever decided was wrong, which is worse than never having organized.
    """
    return MemoryEntry.retired_at.is_(None)


def about_person(person_handle: str) -> ColumnElement[bool]:
    """Every live fact any agent, in any project, holds about this person.

    The question a person's own page asks, and the only one that is not about a
    single pool; see :func:`user_scope_about`.
    """
    return and_(
        MemoryEntry.scope == MemoryScope.user,
        MemoryEntry.scope_id.endswith(user_scope_about(person_handle), autoescape=True),
        live_entries(),
    )


class MemoryStore(Protocol):
    async def recall(
        self, scope: MemoryScope, scope_id: str, limit: int = 50
    ) -> list[str]:
        """Return up to `limit` memory facts, newest ones, oldest first —
        raw facts of every layer."""
        ...

    async def core_and_counts(
        self, pools: list[tuple[MemoryScope, str]]
    ) -> dict[tuple[MemoryScope, str], tuple[int, list[str]]]:
        """Per pool: how many facts it holds, and its core layer oldest-first.

        Several pools at once, because a turn reads one pool per person it is
        sitting with (结论 54) and 总览 seats every member of the project — so
        "one query per pool" is a query count that grows with the team, on the
        path every single turn takes. The count is what injection compares
        against what it carried; the core layer is what it carries.

        A pool with nothing in it may be missing from the result.
        """
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
    # Core that did not fit its own budget. Distinct from ``omitted`` on
    # purpose: a fact left to `recall` is the normal case, a missing *core*
    # fact means the layer that is supposed to be unconditional has stopped
    # being unconditional.
    core_omitted: int = 0


async def recall_pools(
    store: MemoryStore,
    pools: list[tuple[MemoryScope, str]],
    *,
    core_char_budget: int = MEMORY_CORE_CHAR_BUDGET,
) -> RecallResult:
    """The core layer of several pools, as one injection block.

    What is deliberately NOT here is the rest of the pool. Everything else it
    holds comes back as ``omitted`` — a count, not the facts — because the
    failure this guards against is not a missing fact but a reader who cannot
    tell "nothing was ever stored" from "this turn did not ask for it". A
    reader who cannot tell stops asking, and then memory has quietly stopped
    existing no matter how much of it is on disk.
    """
    core: list[str] = []
    core_used = 0
    core_omitted = 0
    stored = 0
    found = await store.core_and_counts(pools)
    for pool in pools:
        count, core_facts = found.get(pool, (0, []))
        stored += count
        kept: list[str] = []
        for fact in reversed(core_facts):
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
    return RecallResult(
        facts=core,
        omitted=max(0, stored - len(core)),
        core_omitted=core_omitted,
    )


# Keyword matching happens in SQL, ranking in Python, so the fetch has to be
# wider than what is returned or a highly-relevant older fact would be cut by
# recency before it is ever scored. It is still a cap: in a pool with more than
# _MIN_CANDIDATES keyword matches, the oldest of them never get ranked. Today's
# pools are ~60 facts, so nothing comes close.
_CANDIDATE_FACTOR = 20
_MIN_CANDIDATES = 200


@dataclass(frozen=True)
class MemoryHit:
    """One search hit: the fact, and an address the caller can quote."""

    uri: str
    abstract: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "abstract": self.abstract, "score": self.score}


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

    async def core_and_counts(
        self, pools: list[tuple[MemoryScope, str]]
    ) -> dict[tuple[MemoryScope, str], tuple[int, list[str]]]:
        """Two queries whatever the number of pools: the counts, then the core.

        Two and not one: the count has to see every row, and fetching every
        fact's text just to count them would put a whole project's memory on
        the wire for the sake of a number that injection only prints.
        """
        if not pools:
            return {}
        # `or_` over `(scope, scope_id)` pairs rather than a row-value `IN`:
        # the pools of one turn do not share a scope, so there is no column to
        # put a list against.
        named = or_(
            *(
                and_(MemoryEntry.scope == scope, MemoryEntry.scope_id == scope_id)
                for scope, scope_id in pools
            )
        )
        counts = await self._session.execute(
            select(MemoryEntry.scope, MemoryEntry.scope_id, func.count())
            .where(named, live_entries())
            .group_by(MemoryEntry.scope, MemoryEntry.scope_id)
        )
        found: dict[tuple[MemoryScope, str], tuple[int, list[str]]] = {
            (scope, scope_id): (int(total), [])
            for scope, scope_id, total in counts.all()
        }
        core = await self._session.scalars(
            select(MemoryEntry)
            .where(named, MemoryEntry.layer == MemoryLayer.core, live_entries())
            .order_by(MemoryEntry.created_at)
        )
        for row in core.all():
            found[(row.scope, row.scope_id)][1].append(row.content)
        return found

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
    return DbMemoryStore(session)
