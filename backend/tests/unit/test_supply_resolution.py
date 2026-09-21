"""Which pool a project runs on (issue #243, 结论 46).

The decision lives in the backend so a project can change supply without
touching the proxy or restarting the sandbox — the previous design pinned the
pool into the sandbox's launch environment, and there is no launch environment
left to pin it into.
"""

from app.domain.agent.supply import GATEWAY, SUBSCRIPTION, resolve_pool


def test_an_explicit_setting_wins():
    assert resolve_pool({"supply": GATEWAY}) == GATEWAY
    assert resolve_pool({"supply": SUBSCRIPTION}) == SUBSCRIPTION


def test_a_project_that_says_nothing_runs_on_the_subscription():
    """A machine is launched in the subscription shape and nothing else, so a
    project that has never chosen runs on the pool that shape reaches first."""
    assert resolve_pool(None) == SUBSCRIPTION
    assert resolve_pool({}) == SUBSCRIPTION
    assert resolve_pool({"other": "x"}) == SUBSCRIPTION


def test_an_unrecognised_value_falls_back_rather_than_failing():
    """A typo in a settings blob must not take a project offline."""
    for junk in ("gatway", "", None, 5, ["gateway"]):
        assert resolve_pool({"supply": junk}) == SUBSCRIPTION
