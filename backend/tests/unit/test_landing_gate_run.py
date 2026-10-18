"""The batch gate's own run is picked among a commit's checks."""

from app.domain.review.landing_forge import Check, gate_run


def _gate(conclusion: str, suite: int) -> Check:
    return Check("CI required", "completed", conclusion, "", suite=suite)


def test_a_cancelled_gate_listed_first_does_not_hide_the_red_one():
    runs = [_gate("cancelled", 2), _gate("failure", 1)]
    assert gate_run(runs).conclusion == "failure"
