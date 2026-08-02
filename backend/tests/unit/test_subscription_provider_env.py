"""The subscription turn must be capturable by the meter.

These lock in facts that were MEASURED on the dev box, each of which silently
produces "works but bills nothing" if it regresses:

  - HTTPS_PROXY alone never sees /v1/messages (undici ignores it), so the
    capture has to be by hostname;
  - a turn with no attribution header is tokens nobody can be charged for;
  - an inherited ANTHROPIC_AUTH_TOKEN switches the CLI out of subscription mode.
"""

import pytest

from app.domain.agent import provider_env


def test_subscription_keeps_proxy_for_the_oauth_traffic():
    """OAuth/refresh DOES honour the proxy env — and on this network a direct
    refresh fails outright, so dropping these would break login, not just billing."""
    choice = provider_env.subscription_provider("http://proxy:3128", "/ca.pem")
    assert choice.env["HTTPS_PROXY"] == "http://proxy:3128"
    assert choice.env["HTTP_PROXY"] == "http://proxy:3128"
    assert choice.env["NODE_EXTRA_CA_CERTS"] == "/ca.pem"


def test_subscription_never_carries_an_api_key():
    """The CLI inherits the backend's env. A leaked ANTHROPIC_AUTH_TOKEN puts it
    in API-key mode, which bills a different account and bypasses the meter —
    so the key must be explicitly blanked, not merely absent."""
    choice = provider_env.subscription_provider("http://proxy:3128", "/ca.pem")
    assert choice.env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_attribution_header_is_emitted_for_the_meter():
    choice = provider_env.subscription_provider(
        "http://proxy:3128", "/ca.pem", project_id="proj-1", topic_id="topic-2"
    )
    assert choice.env["ANTHROPIC_CUSTOM_HEADERS"] == "x-cheese-attr: proj-1/topic-2"


def test_attribution_degrades_to_project_only():
    choice = provider_env.subscription_provider(
        "http://proxy:3128", "/ca.pem", project_id="proj-1"
    )
    assert choice.env["ANTHROPIC_CUSTOM_HEADERS"] == "x-cheese-attr: proj-1"


def test_no_attribution_means_no_header_rather_than_a_broken_one():
    choice = provider_env.subscription_provider("http://proxy:3128", "/ca.pem")
    assert "ANTHROPIC_CUSTOM_HEADERS" not in choice.env


def test_base_url_is_only_set_when_the_meter_needs_a_port():
    """The host must stay api.anthropic.com so what reaches upstream is
    unchanged; base_url exists only to carry a non-443 port."""
    plain = provider_env.subscription_provider("http://proxy:3128", "/ca.pem").env
    assert "ANTHROPIC_BASE_URL" not in plain

    ported = provider_env.subscription_provider(
        "http://proxy:3128", "/ca.pem", base_url="https://api.anthropic.com:8443"
    )
    assert ported.env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com:8443"
    assert "api.anthropic.com" in ported.env["ANTHROPIC_BASE_URL"]


def test_model_names_are_never_pinned_on_the_subscription():
    """Overriding the model aliases makes the official API answer for a model it
    does not serve — the API-key path pins them, this one must not."""
    choice = provider_env.subscription_provider(
        "http://proxy:3128", "/ca.pem", project_id="p", topic_id="t"
    )
    assert not [k for k in choice.env if "MODEL" in k]


@pytest.mark.parametrize("enabled", [True, False])
def test_sandbox_capture_args_follow_the_switch(monkeypatch, enabled):
    """A sandbox must only resolve api.anthropic.com to the meter when the meter
    is actually deployed — otherwise every turn fails on a dead address."""
    from app.core.config import settings
    from app.domain.agent import tmux_provider

    monkeypatch.setattr(settings, "subscription_enabled", enabled)
    monkeypatch.setattr(settings, "subscription_proxy_host", "172.17.0.1")
    monkeypatch.setattr(settings, "subscription_ca_host_path", "/host/ca.pem")

    args = tmux_provider._subscription_args()
    if not enabled:
        assert args == []
        return
    assert "--add-host" in args
    assert "api.anthropic.com:172.17.0.1" in args
    assert "/host/ca.pem:/etc/cheese/proxy-ca.pem:ro" in args
