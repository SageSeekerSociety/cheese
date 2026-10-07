"""A member's own Claude Code signs in with its owner's login, and nothing of
the platform's metering reaches it (#2991).

Its requests go from its owner's machine to the vendor on the login the owner
gave the platform there (`cheesehost claude login`). The platform's credential,
the metering proxy's CA and the tunnel to it are for sessions the project pays
for; put on the owner's machine, they would route the owner's own requests
through the platform, or fail them.
"""

import pytest

from app.core.config import settings
from app.domain.agent.harness.claude_code.device_launch import NO_LOGIN_PLACEHOLDER
from tests.unit.test_device_provider import (
    _no_device_identity,  # noqa: F401 — the machine's address, not the database's
    _subscription_screen,
    _subscription_settings,
)

pytestmark = pytest.mark.anyio

OWN = {"CHEESE_OWN_LOGIN": "1"}


async def test_an_own_session_is_given_nothing_of_the_metering_proxy(
    monkeypatch, tmp_path
):
    ca = _subscription_settings(monkeypatch, tmp_path)
    hub, env, _project, _topic = await _subscription_screen(env=dict(OWN))

    for name in (
        "HTTPS_PROXY",
        "CHEESE_TUNNEL_URL",
        "CHEESE_CONNECT_TOKEN",
        "CHEESE_MODEL_PROXY",
        "NODE_EXTRA_CA_CERTS",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        assert name not in env, name
    launcher = "\n".join(script for _argv, script in hub.execs if script)
    assert ca.strip() not in launcher, "the proxy's CA is not put on the machine"
    assert NO_LOGIN_PLACEHOLDER not in launcher


async def test_an_own_session_reads_the_login_its_owner_gave_the_platform(
    monkeypatch, tmp_path
):
    _subscription_settings(monkeypatch, tmp_path)
    hub, _env, _project, _topic = await _subscription_screen(env=dict(OWN))

    launcher = "\n".join(script for _argv, script in hub.execs if script)
    assert "CLAUDE_SECURESTORAGE_CONFIG_DIR" in launcher
    assert '"$REAL_HOME/.cheese/claude-login"' in launcher


async def test_an_own_session_starts_on_a_deployment_with_no_metering_proxy(
    monkeypatch, tmp_path
):
    """The proxy's CA is a requirement of a subscription session the platform
    pays for; a deployment without one still runs its members' own."""
    monkeypatch.setattr(settings, "subscription_ca_backend_path", "")
    _hub, env, _project, _topic = await _subscription_screen(env=dict(OWN))
    assert "CHEESE_TUNNEL_URL" not in env
