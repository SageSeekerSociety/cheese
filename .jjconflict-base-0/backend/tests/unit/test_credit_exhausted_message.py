"""What a turn says when the AI relay has no money left.

An exhausted balance arrives as HTTP 429 — the same status as a rate limit — so
it fell into the generic branch and told the reader to try again later. Retrying
cannot refill a balance, so that advice sends someone into a loop that can never
succeed, and hides the one thing that has to happen.
"""

import pytest

from app.domain.agent.chat import _is_out_of_credit


@pytest.mark.parametrize(
    "detail",
    [
        # Verbatim from the failure this came from.
        "API Error: Request rejected (429) · [1113][余额不足或无用资源包，请充值。]"
        "[20260731025202bd72819043d8441f]",
        "insufficient balance",
        "Your account has insufficient_quota",
        "quota exceeded for this key",
    ],
)
def test_a_spent_balance_is_recognised(detail):
    assert _is_out_of_credit(detail)


@pytest.mark.parametrize(
    "detail",
    [
        None,
        "",
        "upstream timed out",
        "429 Too Many Requests",  # a real rate limit: waiting DOES help
        "connection reset by peer",
    ],
)
def test_a_transient_failure_is_not_mistaken_for_it(detail):
    """Wrongly calling a blip 'out of credit' would send someone to top up an
    account that is fine — the error in the other direction."""
    assert not _is_out_of_credit(detail)
