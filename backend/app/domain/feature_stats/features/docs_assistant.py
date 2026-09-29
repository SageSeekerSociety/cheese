"""问芝士 的数据页: who came to the docs, who asked, how it went, what it cost.

The first feature to get a page, and the reason the page exists: 「问芝士值不值得
继续」 is a question someone has to answer, and every number needed for it is
already being written by the feature itself.

Two tables, no new tracking:

* ``docs_questions`` — one row per question, with the outcome, the tokens both
  ways, the latency and the page the reader was on. Kept 90 days.
* ``docs_visits`` — one row per visitor per UTC day (``domain/docs_site/visits``),
  recorded by the docs site's beacon. This is the only thing that was added for
  the page: without it there is no denominator, and 「有多少人来了、其中多少
  人用了」 is the top line of the whole report.

The report shape is this module's own, and the front end's page is written
against it row by row — there is no shared block vocabulary to conform to. What
the two agree on is that 「没读到」 is ``null`` and never ``0``, and that a rate
whose denominator is empty is ``null`` too (nobody visited, so 「用问芝士的比
例」 has no value rather than a value of zero).

Privacy is a property of the queries, not of a filter afterwards: the
unanswered-questions list groups by question text and carries the page it was
asked from, and no query in this file selects a user id into the response. A
page that shows question text must never be one join away from showing who
typed it.
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.docs_site.models import DocsQuestion, DocsVisit
from app.domain.feature_stats import pricing, stats
from app.domain.platform_stats.windows import dense_series, utc_day, utc_day_window

logger = logging.getLogger(__name__)

FEATURE_ID = "docs-assistant"
TITLE = "问芝士"
SUMMARY = "文档站的问答助手"

# The most rows the 「答不上来的问题」 table will carry. It is a reading list for
# whoever maintains the docs, not an exhaustive export; past fifty rows the tail
# is one-off phrasings of the same missing page.
UNANSWERED_ROWS = 50
# How many grouped rows are pulled before merging per question. Generous next to
# UNANSWERED_ROWS because one question asked from three pages arrives as three
# rows, and the merge can only rank what it was given.
UNANSWERED_SCAN = 500

# The two outcomes a report cares about by name; anything else is counted as
# 「其他」 rather than dropped, so the parts still add up to the whole.
ANSWERED = "answered"
NO_MATCH = "no_match"
FAILED = "failed"


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    """``None`` when the denominator is empty — a rate with no base is not 0."""
    if not denominator or numerator is None:
        return None
    return numerator / denominator


async def _visits(session: AsyncSession, since, until) -> dict:
    """Who came: distinct visitors, how many of them signed in, and the daily shape."""
    window = (DocsVisit.day >= since.date(), DocsVisit.day < until.date())
    totals = (
        await session.execute(
            select(
                func.count(func.distinct(DocsVisit.visitor_id)),
                func.count(func.distinct(DocsVisit.user_id)),
            ).where(*window)
        )
    ).one()
    per_day = (
        await session.execute(
            select(DocsVisit.day, func.count()).where(*window).group_by(DocsVisit.day)
        )
    ).all()
    return {
        "visitors": int(totals[0] or 0),
        "logged_in": int(totals[1] or 0),
        "by_day": {row[0]: int(row[1]) for row in per_day},
    }


async def _questions(session: AsyncSession, since, until) -> dict:
    """Who asked, how many questions, how they turned out, and the daily shape."""
    window = (DocsQuestion.created_at >= since, DocsQuestion.created_at < until)
    outcomes = (
        await session.execute(
            select(DocsQuestion.outcome, func.count())
            .where(*window)
            .group_by(DocsQuestion.outcome)
        )
    ).all()
    counts = {str(row[0]): int(row[1]) for row in outcomes}
    total = sum(counts.values())
    askers = int(
        (
            await session.execute(
                select(func.count(func.distinct(DocsQuestion.user_id))).where(*window)
            )
        ).scalar()
        or 0
    )
    # One expression object, used for both the projection and the grouping: two
    # calls to `utc_day` would compile to two sets of bind parameters, and
    # PostgreSQL matches GROUP BY by expression, not by shape. ``day.label`` then
    # names the column so the row reads ``row[0]`` either way.
    day = utc_day(DocsQuestion.created_at)
    per_day = (
        await session.execute(
            select(
                day.label("day"),
                func.count(),
                func.count(func.distinct(DocsQuestion.user_id)),
            )
            .where(*window)
            .group_by(day)
        )
    ).all()
    return {
        "total": total,
        "askers": askers,
        "answered": counts.get(ANSWERED, 0),
        "no_match": counts.get(NO_MATCH, 0),
        "failed": counts.get(FAILED, 0),
        "by_day": {row[0].date(): int(row[1]) for row in per_day},
        "askers_by_day": {row[0].date(): int(row[2]) for row in per_day},
    }


async def _tokens_and_latency(session: AsyncSession, since, until) -> dict:
    """Every question's total tokens and latency, read as two lists.

    One query, three int columns: the 90-day window holds a few thousand rows at
    this feature's scale, and the alternative — quantiles in SQL plus a second
    pass for the histogram — costs more to maintain than it saves. Rows whose
    token counts are NULL (recorded before the column, or a call that never
    reached the model) are left out rather than counted as zero.
    """
    rows = (
        await session.execute(
            select(
                DocsQuestion.prompt_tokens,
                DocsQuestion.completion_tokens,
                DocsQuestion.latency_ms,
                DocsQuestion.model,
            ).where(DocsQuestion.created_at >= since, DocsQuestion.created_at < until)
        )
    ).all()
    tokens: list[int] = []
    latency: list[int] = []
    by_model: dict[str, list[int]] = {}
    for prompt, completion, ms, model in rows:
        if prompt is not None or completion is not None:
            total = int(prompt or 0) + int(completion or 0)
            tokens.append(total)
            bucket = by_model.setdefault(str(model or ""), [0, 0])
            bucket[0] += int(prompt or 0)
            bucket[1] += int(completion or 0)
        latency.append(int(ms or 0))
    return {
        "tokens": tokens,
        "latency": latency,
        "tokens_by_model": {
            name: (pair[0], pair[1]) for name, pair in by_model.items()
        },
    }


async def _unanswered(session: AsyncSession, since, until) -> list[dict]:
    """What the docs could not answer, most-asked first — without who asked.

    Grouped by ``(question, page)`` in SQL and merged in Python: the same
    question asked from three pages is one row in the table with the busiest
    page beside it, and its count has to be the sum of the three, which is a
    thing SQL would need a window function to say and Python says in six lines.
    """
    rows = (
        await session.execute(
            select(DocsQuestion.question, DocsQuestion.page, func.count())
            .where(
                DocsQuestion.created_at >= since,
                DocsQuestion.created_at < until,
                DocsQuestion.outcome == NO_MATCH,
            )
            .group_by(DocsQuestion.question, DocsQuestion.page)
            .order_by(func.count().desc())
            .limit(UNANSWERED_SCAN)
        )
    ).all()
    merged: dict[str, dict] = {}
    for question, page, count in rows:
        entry = merged.get(question)
        if entry is None:
            merged[question] = {"question": question, "page": page, "count": int(count)}
        else:
            entry["count"] += int(count)
            if entry["page"] is None:
                entry["page"] = page
    return sorted(merged.values(), key=lambda row: row["count"], reverse=True)[
        :UNANSWERED_ROWS
    ]


def _iso(day: date) -> str:
    return day.isoformat()


async def load(session: AsyncSession, *, days: int) -> dict[str, Any]:
    """The whole page for one window. See the module docstring for the shape."""
    since, until, buckets = utc_day_window(days)
    visits = await _visits(session, since, until)
    questions = await _questions(session, since, until)
    measured = await _tokens_and_latency(session, since, until)
    cost = await pricing.estimate(measured["tokens_by_model"])

    total = questions["total"]
    answered = questions["answered"]
    cost_usd = cost["usd"]
    per_question = cost_usd / total if cost_usd is not None and total else None
    return {
        "id": FEATURE_ID,
        "title": TITLE,
        "summary": SUMMARY,
        "days": days,
        "start": _iso(buckets[0]),
        "end": _iso(buckets[-1]),
        "numbers": {
            "visitors": {"value": visits["visitors"], "logged_in": visits["logged_in"]},
            "askers": {
                "value": questions["askers"],
                # The denominator is signed-in visitors: 问芝士 needs an account,
                # so counting against everyone who read the docs would make the
                # share fall whenever a signed-out reader arrived.
                "share": _ratio(questions["askers"], visits["logged_in"]),
            },
            "questions": {
                "value": total,
                "per_asker": _ratio(total, questions["askers"]),
            },
            "answer_rate": {
                "value": _ratio(answered, total),
                "answered": answered,
                "total": total,
            },
            "cost": {
                "usd": cost_usd,
                "per_question": per_question,
                "source": cost["source"],
                "unpriced_tokens": cost["unpriced_tokens"],
            },
        },
        # Three lines on one chart, and they are three different questions: how
        # many people came, how many of them asked, and how much they asked.
        # Days with nothing are filled with 0 by ``dense_series`` — from the
        # window, never from the days that happen to have rows, or a quiet week
        # would draw as a shorter line and nobody would notice.
        "trend": dense_series(
            buckets,
            {
                "visitors": visits["by_day"],
                "askers": questions["askers_by_day"],
                "questions": questions["by_day"],
            },
        ),
        "tokens": {
            **stats.describe(measured["tokens"]),
            "histogram": stats.histogram(measured["tokens"]),
        },
        "latency": stats.describe(measured["latency"]),
        "outcomes": {
            "answered": answered,
            "no_match": questions["no_match"],
            "failed": questions["failed"],
            "total": total,
        },
        "unanswered": await _unanswered(session, since, until),
    }
