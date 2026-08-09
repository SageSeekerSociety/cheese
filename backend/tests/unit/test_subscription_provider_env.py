"""The subscription turn must be capturable by the meter.

These lock in facts that were MEASURED on the dev box, each of which silently
produces "works but bills nothing" if it regresses:

  - capture is by hostname (BASE_URL host stays api.anthropic.com), because
    Claude Code's undici ignores HTTPS_PROXY for /v1/messages;
  - a turn with no attribution header is tokens nobody can be charged for;
  - an inherited ANTHROPIC_AUTH_TOKEN switches the CLI out of subscription mode.
"""

import pytest

from app.domain.agent import provider_env


def test_subscription_sets_no_base_url_so_it_stays_oauth():
    """A BASE_URL flips interactive Claude Code into API-key mode and it ignores
    the OAuth credential ("Not logged in"). --add-host on 443 does the routing."""
    choice = provider_env.subscription_provider(ca_path="/ca.pem")
    assert "ANTHROPIC_BASE_URL" not in choice.env
    assert choice.env["NODE_EXTRA_CA_CERTS"] == "/ca.pem"


def test_subscription_never_carries_an_api_key():
    """The CLI inherits the backend's env. A leaked ANTHROPIC_AUTH_TOKEN puts it
    in API-key mode, which bills a different account and bypasses the meter —
    so the key must be explicitly blanked, not merely absent."""
    choice = provider_env.subscription_provider(ca_path="/ca.pem")
    assert choice.env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_login_is_a_placeholder_oauth_token_not_a_real_one():
    """Login is via CLAUDE_CODE_OAUTH_TOKEN — the env var the CLI accepts without
    the local validation a .credentials.json gets. It must be an obvious
    placeholder that authenticates nothing; the proxy swaps in the real token."""
    env = provider_env.subscription_provider(ca_path="/ca.pem").env
    tok = env["CLAUDE_CODE_OAUTH_TOKEN"]
    assert "placeholder" in tok
    assert tok == provider_env.SUBSCRIPTION_PLACEHOLDER_TOKEN


def test_session_token_is_carried_as_the_bearer_when_given():
    """Once the proxy is reachable beyond the box's own bridge, the placeholder
    (public in this repo) becomes a way in — so a per-session scoped token rides
    as the Bearer instead. The proxy verifies it before spending the sub."""
    env = provider_env.subscription_provider(
        ca_path="/ca.pem", session_token="scoped.abc123"
    ).env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "scoped.abc123"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] != provider_env.SUBSCRIPTION_PLACEHOLDER_TOKEN


def test_the_carried_token_is_one_the_proxy_can_verify():
    """The scoped token the container carries must be verifiable with the shared
    signing secret AND expose the project/topic to bill — this is what lets the
    proxy take attribution from the token instead of the spoofable header."""
    from app.core.sandbox_auth import mint_scoped_token, scoped_token_claims

    token = mint_scoped_token(project_id="proj-1", topic_id="topic-2")
    env = provider_env.subscription_provider(
        ca_path="/ca.pem", project_id="proj-1", topic_id="topic-2", session_token=token
    ).env
    claims = scoped_token_claims(env["CLAUDE_CODE_OAUTH_TOKEN"])
    assert claims is not None  # good signature, unexpired
    assert claims["p"] == "proj-1"
    assert claims["t"] == "topic-2"


def test_attribution_header_is_emitted_for_the_meter():
    choice = provider_env.subscription_provider(
        ca_path="/ca.pem", project_id="proj-1", topic_id="topic-2"
    )
    assert choice.env["ANTHROPIC_CUSTOM_HEADERS"] == "x-cheese-attr: proj-1/topic-2"


def test_attribution_degrades_to_project_only():
    choice = provider_env.subscription_provider(ca_path="/ca.pem", project_id="proj-1")
    assert choice.env["ANTHROPIC_CUSTOM_HEADERS"] == "x-cheese-attr: proj-1"


def test_no_attribution_means_no_header_rather_than_a_broken_one():
    choice = provider_env.subscription_provider(ca_path="/ca.pem")
    assert "ANTHROPIC_CUSTOM_HEADERS" not in choice.env


def test_no_base_url_is_ever_set():
    """The routing is DNS (--add-host), never a base_url — setting one drops the
    CLI out of subscription mode."""
    env = provider_env.subscription_provider(ca_path="/ca.pem").env
    assert "ANTHROPIC_BASE_URL" not in env


def test_model_names_are_never_pinned_on_the_subscription():
    """Overriding the model aliases makes the official API answer for a model it
    does not serve — the API-key path pins them, this one must not."""
    choice = provider_env.subscription_provider(
        ca_path="/ca.pem", project_id="p", topic_id="t"
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
    # Messages AND the login/refresh hosts all route to the meter.
    assert "api.anthropic.com:172.17.0.1" in args
    assert "console.anthropic.com:172.17.0.1" in args
    assert "platform.claude.com:172.17.0.1" in args
    assert "/host/ca.pem:/etc/cheese/proxy-ca.pem:ro" in args


def test_no_credential_file_is_ever_planted_in_the_box(monkeypatch, tmp_path):
    """Hard requirement: a sandbox must not hold a valid credential. Login is via
    the CLAUDE_CODE_OAUTH_TOKEN env placeholder, so NO .credentials.json is
    written — not even a fake one (the file gets a local validation that rejected
    the placeholder, and a real token there would be the very leak we forbid)."""
    from app.core.config import settings
    from app.domain.agent.tmux_provider import TmuxHooksProvider

    provider = TmuxHooksProvider(image="x", idle_suspect_s=1.0, hard_ceiling_s=1.0)
    monkeypatch.setattr(settings, "subscription_enabled", True)
    provider._write_session_settings(str(tmp_path))
    assert not (tmp_path / ".credentials.json").exists()
