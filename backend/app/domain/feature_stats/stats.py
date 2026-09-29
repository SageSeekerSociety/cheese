"""The arithmetic behind a feature page's numbers, over plain Python lists.

Deliberately not SQL. The projections here are 「一次提问花了多少 tokens」 and
「答一次用了多久」 — one number per row, on tables that keep 90 days. That is
small enough to read whole and compute in the process, and doing it here rather
than in ``percentile_cont`` buys two things worth more than the rows saved:
the same code runs under the test suite's SQLite-free integration database as
under production, and the histogram is binned from the very list the quantiles
came from — a histogram and a median that disagree about their inputs is a bug
nobody sees.

Every function returns ``None`` for 「no data」 and never ``0``: a feature with
no questions has no median token count, and printing one would be a lie told in
the same font as a real answer.
"""

import math


def percentile(values: list[float] | list[int], q: float) -> float | None:
    """The ``q``-th quantile (0–1), linearly interpolated, or None.

    Interpolation matches PostgreSQL's ``percentile_cont`` and NumPy's default,
    so an implementation of this page that pushed the work into SQL would print
    the same number. The naive alternatives (nearest-rank, or an index that
    forgets the ``n-1``) read as 「roughly right」 and then disagree with the
    dashboard by one bin forever.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (pos - low))


def describe(values: list[int]) -> dict:
    """The five numbers a distribution block shows, plus how many fed it."""
    if not values:
        return {
            "count": 0,
            "avg": None,
            "median": None,
            "min": None,
            "p90": None,
            "max": None,
        }
    return {
        "count": len(values),
        "avg": sum(values) / len(values),
        "median": percentile(values, 0.5),
        "min": float(min(values)),
        "p90": percentile(values, 0.9),
        "max": float(max(values)),
    }


def histogram(values: list[int], bins: int = 10) -> list[dict]:
    """``bins`` equal-width buckets over ``[0, max]``: ``{from, to, count}``.

    Empty when there is nothing to bin. Every bar belongs to exactly one bucket
    — the top edge of the last one is included, so the maximum lands in it
    rather than past the end of the chart.
    """
    if not values:
        return []
    top = max(values)
    if top <= 0:
        return [{"from": 0.0, "to": 0.0, "count": len(values)}]
    width = top / bins
    out = [{"from": i * width, "to": (i + 1) * width, "count": 0} for i in range(bins)]
    for value in values:
        index = min(bins - 1, int(value / width))
        out[index]["count"] += 1
    return out
