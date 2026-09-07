"""A project picks its subscription model the way it picks a compute pool.

Sonnet 5 is the default (balanced, saves the subscription's quota); Opus 5 is the
explicit opt-in. The default must carry NO --model flag — that is the proven path
(the subscription's own default), and pinning a name it doesn't serve fails a turn.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


def test_non_default_models_pin_full_ids():
    # Full ids, not CLI aliases: Fable degrades to Opus 4.8 specifically, so
    # 4.8-vs-5 must be pickable and visible — a bare "opus" alias hides which
    # generation actually serves.
    assert subscription_model_alias("opus") == "claude-opus-5"
    assert subscription_model_alias("opus-4.8") == "claude-opus-4-8"
    assert subscription_model_alias("fable") == "claude-fable-5"


def test_unknown_selection_falls_back_to_default_not_error():
    # A stale stored selection must not break a turn.
    assert subscription_model_alias("gpt-9") == ""


def test_listing_shows_both_with_sonnet_default():
    listings = {p.id: p for p in subscription_model_listings()}
    assert subscription_model_ids() == {"sonnet", "opus", "opus-4.8", "fable"}
    assert listings["sonnet"].default is True
    assert listings["opus"].default is False
    assert listings["fable"].default is False
    assert "Opus 4.8" in listings["fable"].description  # the real fallback target
    assert listings["opus-4.8"].default is False
    assert all(p.available for p in listings.values())
    assert listings["sonnet"].kind == "model"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("enabled", "supply", "expected"),
    [
        (True, None, "subscription"),
        (False, None, "gateway"),
        (True, "gateway", "gateway"),
        (False, "subscription", "subscription"),
    ],
)
async def test_project_model_choices_follow_supply(
    monkeypatch, enabled, supply, expected
):
    from app.api.routes import projects
    from app.core.errors import ValidationError

    project = SimpleNamespace(settings={"subscription_model": "opus", "supply": supply})
    monkeypatch.setattr(projects.settings, "subscription_enabled", enabled)
    monkeypatch.setattr(
        projects.ProjectRepository, "get", AsyncMock(return_value=project)
    )
    db = AsyncMock()
    project_id = uuid.uuid4()

    listing = (await projects.list_model_profiles(project_id, db))["data"]
    assert listing["supply"] == expected
    if expected == "gateway":
        assert listing["current"] is None
        assert listing["profiles"] == []
        with pytest.raises(ValidationError):
            await projects.set_model_profile(project_id, {"profile": "fable"}, db)
        assert project.settings["subscription_model"] == "opus"
        db.flush.assert_not_awaited()
    else:
        assert listing["current"] == "opus"
        assert "opus" in {p["id"] for p in listing["profiles"]}
        await projects.set_model_profile(project_id, {"profile": "fable"}, db)
        updated = (await projects.list_model_profiles(project_id, db))["data"]
        assert updated["current"] == "fable"
        db.flush.assert_awaited_once()
