"""Refusing a turn that cannot be afforded.

Reading the transcript afterwards says what a turn cost once it is over — enough
to answer "where did the money go", which is what was missing, but useless
against the turn that empties the account. By the time the number exists it has
been spent. Enforcement has to precede the request.
"""

import pytest

from app.domain.agent.budget_proxy import (
    BudgetState,
    decide,
    should_refuse_connection,
)


def test_a_project_within_budget_proceeds():
    d = decide(BudgetState(spent_usd=1.0, limit_usd=5.0))

    assert d.allow
    assert "4.0000" in d.reason, d.reason


def test_a_spent_budget_is_refused_and_says_so():
    """A refusal must be legible: 'over budget' with no numbers sends someone
    to guess at a dashboard that does not exist yet."""
    d = decide(BudgetState(spent_usd=5.0, limit_usd=5.0))

    assert not d.allow
    assert "5.0000" in d.reason, d.reason
    assert should_refuse_connection(BudgetState(spent_usd=5.0, limit_usd=5.0))


def test_an_unlimited_project_is_never_refused():
    """自治项目 have no grant and must not be throttled by a brake meant for
    metered ones — a guard that stops legitimate work is its own outage, and
    this repo has already shipped that mistake twice this week."""
    assert decide(BudgetState(spent_usd=10_000.0, limit_usd=None)).allow
    assert not should_refuse_connection(BudgetState(spent_usd=10_000.0, limit_usd=None))


@pytest.mark.parametrize(
    ("spent", "limit", "allowed"),
    [
        (0.0, 0.0, False),  # a zero budget is a real limit, not "unset"
        (4.9999, 5.0, True),
        (5.0001, 5.0, False),
    ],
)
def test_the_boundary_is_where_it_says_it_is(spent, limit, allowed):
    assert decide(BudgetState(spent_usd=spent, limit_usd=limit)).allow is allowed
