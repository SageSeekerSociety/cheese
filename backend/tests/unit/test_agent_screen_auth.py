"""Unit tests for the agent-screen auth fallback in get_current_user_id.

一个 agent 就是一个 user: a call from inside an agent screen (device token as
bearer + X-Cheese-Screen) must authorize as the screen's agent user on the general
API, while humans (valid JWT) are unaffected and bare/unknown tokens still 401.
No DB, no real hub — resolve_actor is patched.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.common.auth as auth
from app.common.auth import create_access_token, get_current_user_id
from app.core.errors import AuthenticationRequiredError

pytestmark = pytest.mark.anyio


async def test_human_jwt_still_resolves(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(auth, "_resolve_agent_screen_actor", called)
    token = create_access_token(4242)
    uid = await get_current_user_id(authorization=f"Bearer {token}")
    assert uid == 4242
    called.assert_not_awaited()  # never falls through when the JWT is valid


async def test_agent_screen_token_resolves_to_agent_user(monkeypatch):
    async def fake_resolve(device_token, screen_token):
        assert device_token == "dev-tok"
        assert screen_token == "screen-tok"
        return 9209

    monkeypatch.setattr(auth, "_resolve_agent_screen_actor", fake_resolve)
    uid = await get_current_user_id(
        authorization="Bearer dev-tok", x_cheese_screen="screen-tok"
    )
    assert uid == 9209


async def test_bad_token_without_screen_still_401(monkeypatch):
    monkeypatch.setattr(auth, "_resolve_agent_screen_actor", AsyncMock(return_value=None))
    with pytest.raises(AuthenticationRequiredError):
        await get_current_user_id(authorization="Bearer not-a-jwt")


async def test_resolve_helper_requires_inside_screen(monkeypatch):
    # A bare device token (no screen) must NOT become a general API credential.
    async def fake_resolve_actor(device_service, hub, *, device_token, screen_token):
        return SimpleNamespace(actor_user_id=1, inside_screen=False)

    monkeypatch.setattr("app.agent.attribution.resolve_actor", fake_resolve_actor)
    monkeypatch.setattr("app.agent.connector_plane.device_service", lambda: object())
    monkeypatch.setattr("app.agent.connector_plane.hub", lambda: object())
    assert await auth._resolve_agent_screen_actor("dev", "screen") is None


async def test_resolve_helper_returns_agent_when_inside_screen(monkeypatch):
    async def fake_resolve_actor(device_service, hub, *, device_token, screen_token):
        return SimpleNamespace(actor_user_id=777, inside_screen=True)

    monkeypatch.setattr("app.agent.attribution.resolve_actor", fake_resolve_actor)
    monkeypatch.setattr("app.agent.connector_plane.device_service", lambda: object())
    monkeypatch.setattr("app.agent.connector_plane.hub", lambda: object())
    assert await auth._resolve_agent_screen_actor("dev", "screen") == 777


async def test_resolve_helper_no_screen_token_is_none():
    assert await auth._resolve_agent_screen_actor("dev", "") is None
