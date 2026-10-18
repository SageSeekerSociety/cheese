"""Counting SQL round trips — the one place that knows how.

A test in this family asserts on **how many statements the database saw**, not
on the answer. An endpoint that reads one row per listed item returns exactly
the right JSON; the only thing that separates it from the batched one is the
round-trip count. Eighteen tests already did this, each with its own 60–200-line
fake session and its own name for it, and the name lived in the file name and
nowhere in the code — which is why the family could not be found by searching
for it.

Everything that counts now goes through :func:`counting_sql`, through
:func:`assert_does_not_scale` when the question is the shape of the growth
rather than an exact number, or through :func:`assert_reads_within_ceiling`
when the growth is a known N+1 someone has yet to batch. The ``reads_once``
marker that lists the family
(`pytest -m reads_once`) is added by the collector in ``tests/conftest.py``
from :func:`measures_query_counts`, defined here so the marker and the helper
cannot disagree about what the family is.

Counting listens on the engine the requests are actually served from, not on
the ``Engine`` class: the app starts its own work as it comes up (taking over a
previous process's turns, sweeping storage) on an engine of its own, and on a
slow machine those statements land inside the block and are charged to the
request.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event

#: A file name saying "this reads once" is one way to join the family. The bare
#: ``once`` is deliberately broad: ten of its matches are the family proper
#: (``test_channel_directory_reads_owners_once``, ``test_live_inputs_reads_once``
#: …) and only a handful are idempotency tests ("the beat is remembered once").
#: Narrowing it to ``reads_once``/``read_once`` would drop those ten from
#: ``-m reads_once``, which is the roster the marker exists to publish. The
#: marker's description says "read or happen exactly once" for that reason.
NAME_HINTS = ("reads_once", "read_once", "once")

#: Saying it in the code is the other, and the one that actually scales: a new
#: test gets the marker without anyone remembering the naming convention. These
#: are the spellings in the tree — both cursor hooks (the SQLAlchemy event the
#: counter hangs on) and the three helper names people gave the counter itself.
CONTENT_HINTS = (
    "before_cursor_execute",
    "after_cursor_execute",
    "counting_sql",
    "count_sql",
    "count_queries",
    "query_count",
    # A hand-written fake session that tallies statements per table — the same
    # idea spelled without the SQLAlchemy events, e.g.
    # tests/unit/test_material_readable_ids_is_batched.py.
    "_CountingSession",
)

_SOURCE_CACHE: dict[str, str] = {}


@contextmanager
def counting_sql(client: Any) -> Iterator[list[str]]:
    """Every SQL statement the app's requests issued while the block runs, in order.

    ``client`` is the integration harness's ``TestClient``; its
    ``test_app_engine`` is the engine the app's requests run on.
    """
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    engine = client.test_app_engine.sync_engine
    event.listen(engine, "after_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine, "after_cursor_execute", _record)


class QueryGrowth(AssertionError):
    """A list's statement count grew with the number of rows it listed.

    Raised by :func:`assert_growth_within` when a page that must not scale
    does, and by :func:`assert_reads_within_ceiling` when a page whose debt is
    recorded goes past the count recorded for it. Its own type in both cases so
    a caller can tell this defect from an ordinary assertion — a seed that
    failed, a 500, or a response shape that moved is not this — and so a
    failure line names the defect rather than a boolean.

    It used to be its own type so that
    ``pytest.mark.xfail(raises=QueryGrowth)`` could excuse exactly this defect.
    That has no use in this repository: an ``xfail`` is reported as a *skipped
    case* and ``scripts/assert_suite_ran.py`` fails the job on a skip,
    deliberately. Known debt is recorded as a ceiling instead — see
    :func:`assert_reads_within_ceiling` — and ``tests/unit/test_known_debt_is_not_an_xfail.py``
    keeps the marker out of the tree.
    """


def count_sql(client: Any, request: Callable[[], Any]) -> tuple[int, Any]:
    """Run ``request`` once and return how many statements it issued, plus its
    response."""
    with counting_sql(client) as seen:
        response = request()
    return len(seen), response


def assert_does_not_scale(
    client: Any,
    *,
    seed: Callable[[int], None],
    request: Callable[[], Any],
    small: int = 3,
    large: int = 30,
    allow_growth: int = 2,
    what: str = "the listed collection",
) -> tuple[int, int]:
    """The list must cost the same at ``large`` rows as at ``small`` rows.

    ``seed(n)`` makes ``what`` hold at least ``n`` rows; ``request()`` performs
    the list call and returns its response (which must be a 200). The assertion
    is on the **difference**, not on either count: the absolute number is a
    property of the endpoint and belongs in nobody's test, while a positive
    difference is the N+1 itself.

    ``allow_growth`` is not slack for a growing query. It is room for the
    constant statements a session adds on its own (a permission probe, a
    savepoint) and is deliberately small: anything proportional fails.
    """
    seed(small)
    small_count, small_response = count_sql(client, request)
    assert small_response.status_code == 200, small_response.text
    seed(large)
    large_count, large_response = count_sql(client, request)
    assert large_response.status_code == 200, large_response.text

    assert_growth_within(
        small, large, small_count, large_count, allow_growth=allow_growth, what=what
    )
    return small_count, large_count


def assert_growth_within(
    small: int,
    large: int,
    small_count: int,
    large_count: int,
    *,
    allow_growth: int = 2,
    what: str = "the listed collection",
) -> None:
    """The same judgement on two counts someone measured themselves.

    :func:`assert_does_not_scale` seeds and measures; a test that has to build
    two differently-sized fixtures by hand (a task per submission count, say)
    measures its own and calls this, so a page whose debt is recorded uses the
    same :class:`QueryGrowth` as one that must not scale at all.
    """
    grew = large_count - small_count
    if grew > allow_growth:
        raise QueryGrowth(
            f"{what} grew from {small} to {large} rows and the statement count "
            f"went {small_count} -> {large_count} (+{grew}); something is "
            "querying per row."
        )


def assert_reads_within_ceiling(
    count: int,
    *,
    ceiling: int,
    what: str,
    debt: str,
) -> None:
    """A list whose N+1 is known and recorded may not read more than ``ceiling``.

    :func:`assert_does_not_scale` is the rule for a page that must not scale.
    This is for the page that does scale today and whose batching is a separate
    change: the count measured now becomes a ceiling, so the test is green
    where it stands, red the moment the endpoint reads even one row more, and
    green again only after the batch lands and the ceiling comes down with it.

    ``debt`` says which N+1 this is and what pays it off, and goes into the
    failure message: whoever trips the ceiling needs to know whether they grew
    the debt or merely met it, and the number alone does not say.

    Why not ``pytest.mark.xfail``. A marker records a debt by *not running the
    assertion*: pytest reports it as skipped, and
    ``backend/scripts/assert_suite_ran.py`` fails the job on any skipped case —
    deliberately, so a suite that cannot meet its environment is loud rather
    than green. An xfail is therefore not a way to record debt here, and
    ``tests/unit/test_known_debt_is_not_an_xfail.py`` keeps it out of the tree.
    A ceiling records the same debt as a number that is compared on every run,
    which is strictly more information: the endpoint's cost is asserted at the
    size the test seeds, not excused.
    """
    if count > ceiling:
        raise QueryGrowth(
            f"{what} read {count} statements, over the {ceiling} recorded for it "
            f"({debt}). Either something new is querying per row — batch it — or "
            "the debt was paid off and this ceiling is stale: lower it to the "
            "count measured now, in the same change."
        )


def _source(path: str) -> str:
    text = _SOURCE_CACHE.get(path)
    if text is None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        _SOURCE_CACHE[path] = text
    return text


def name_says_reads_once(path: str) -> bool:
    """Whether the file name alone claims membership."""
    name = os.path.basename(path).lower()
    return any(hint in name for hint in NAME_HINTS)


def code_counts_statements(path: str) -> bool:
    """Whether the file's own code counts statements.

    This is the half a name cannot cover: the family grows every time someone
    writes a new counter, and nobody will remember to name the file for it.
    """
    return any(hint in _source(path) for hint in CONTENT_HINTS)


def measures_query_counts(path: str) -> bool:
    """Whether a test file belongs to the "reads once" family."""
    return name_says_reads_once(path) or code_counts_statements(path)
