"""Which pool a project runs on (issue #243).

The decision lives in the backend so a project can change supply without
touching the proxy or restarting the sandbox — the previous design pinned the
pool into the sandbox's launch environment.
"""

from app.domain.agent.supply import GATEWAY, SUBSCRIPTION, resolve_pool


def test_an_explicit_setting_wins():
    assert resolve_pool({"supply": GATEWAY}, subscription_enabled=True) == GATEWAY
    assert (
        resolve_pool({"supply": SUBSCRIPTION}, subscription_enabled=False)
        == SUBSCRIPTION
    )


def test_no_setting_follows_the_deployment_default():
    """Every project that predates this field keeps exactly the supply it has
    today — the migration is a no-op by construction."""
    assert resolve_pool(None, subscription_enabled=True) == SUBSCRIPTION
    assert resolve_pool({}, subscription_enabled=False) == GATEWAY
    assert resolve_pool({"other": "x"}, subscription_enabled=True) == SUBSCRIPTION


def test_an_unrecognised_value_falls_back_rather_than_failing():
    """A typo in a settings blob must not take a project offline; the fallback
    is always a pool this deployment can actually serve."""
    for junk in ("gatway", "", None, 5, ["gateway"]):
        assert resolve_pool({"supply": junk}, subscription_enabled=True) == SUBSCRIPTION
        assert resolve_pool({"supply": junk}, subscription_enabled=False) == GATEWAY
