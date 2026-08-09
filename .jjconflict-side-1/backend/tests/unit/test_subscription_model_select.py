"""A project picks its subscription model the way it picks a compute pool.

Sonnet 5 is the default (balanced, saves the subscription's quota); Opus 5 is the
explicit opt-in. The default must carry NO --model flag — that is the proven path
(the subscription's own default), and pinning a name it doesn't serve fails a turn.
"""

from app.domain.agent.market import (
    subscription_model_alias,
    subscription_model_default,
    subscription_model_ids,
    subscription_model_listings,
)


def test_default_is_sonnet_and_carries_no_model_flag():
    assert subscription_model_default() == "sonnet"
    # "" → the tmux launcher passes no --model, i.e. the subscription default.
    assert subscription_model_alias("sonnet") == ""
    assert subscription_model_alias(None) == ""


def test_opus_is_an_explicit_opt_in_alias():
    assert subscription_model_alias("opus") == "opus"


def test_unknown_selection_falls_back_to_default_not_error():
    # A stale stored selection must not break a turn.
    assert subscription_model_alias("gpt-9") == ""


def test_listing_shows_both_with_sonnet_default():
    listings = {p.id: p for p in subscription_model_listings()}
    assert subscription_model_ids() == {"sonnet", "opus"}
    assert listings["sonnet"].default is True
    assert listings["opus"].default is False
    assert all(p.available for p in listings.values())
    assert listings["sonnet"].kind == "model"
