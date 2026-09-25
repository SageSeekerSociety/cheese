"""The subscription turn must be capturable by the meter.

These lock in facts that were MEASURED, each of which silently produces "works
but bills nothing" if it regresses:

  - BASE_URL stays unset (api.anthropic.com): sessions reach the meter by
    HTTPS_PROXY at its CONNECT listener (the native CLI honors it — measured
    2026-08-13 on 2.1.229);
  - a turn with no attribution header is tokens nobody can be charged for;
  - an inherited ANTHROPIC_AUTH_TOKEN switches the CLI out of subscription mode.
"""

from app.domain.agent import provider_env


def test_subscription_sets_no_base_url_so_it_stays_oauth():
    """A BASE_URL flips interactive Claude Code into API-key mode and it ignores
    the OAuth login ("Not logged in"). HTTPS_PROXY does the routing."""
    choice = provider_env.subscription_provider(ca_path="/ca.pem")
    assert "ANTHROPIC_BASE_URL" not in choice.env
    assert choice.env["NODE_EXTRA_CA_CERTS"] == "/ca.pem"


def test_subscription_never_carries_an_api_key():
    """The CLI inherits the backend's env. A leaked ANTHROPIC_AUTH_TOKEN puts it
    in API-key mode, which bills a different account and bypasses the meter —
    so the key must be explicitly blanked, not merely absent."""
    choice = provider_env.subscription_provider(ca_path="/ca.pem")
    assert choice.env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_the_environment_carries_no_claude_login_of_its_own():
    """The session logs in with its host's own credential, chosen by the launch
    script. A login in this env would win over that store and never refresh."""
    env = provider_env.subscription_provider(
        ca_path="/ca.pem",
        project_id="proj-1",
        topic_id="topic-2",
        connect_proxy_url="http://cheese:scoped.tok@172.17.0.1:8444",
    ).env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


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
    """The routing is a CONNECT proxy, never a base_url — setting one drops the
    CLI out of subscription mode."""
    env = provider_env.subscription_provider(ca_path="/ca.pem").env
    assert "ANTHROPIC_BASE_URL" not in env
    env = provider_env.subscription_provider(
        ca_path="/ca.pem", connect_proxy_url="http://cheese:t@172.17.0.1:8444"
    ).env
    assert "ANTHROPIC_BASE_URL" not in env


def test_connect_transport_rides_https_proxy_with_its_exclusions():
    """A bare device process is steered by HTTPS_PROXY (no root, no --add-host),
    and NO_PROXY must keep the backend + loopback out of the detour — the CLI
    routes even plain-http requests through HTTPS_PROXY (measured)."""
    env = provider_env.subscription_provider(
        ca_path="/ca.pem",
        connect_proxy_url="http://cheese:tok@172.17.0.1:8444",
        no_proxy="cheese.test,localhost,127.0.0.1,::1",
    ).env
    assert env["HTTPS_PROXY"] == "http://cheese:tok@172.17.0.1:8444"
    assert env["NO_PROXY"] == "cheese.test,localhost,127.0.0.1,::1"
    assert env["no_proxy"] == env["NO_PROXY"]


def test_model_names_are_never_pinned_on_the_subscription():
    """Overriding the model aliases makes the official API answer for a model it
    does not serve — the API-key path pins them, this one must not."""
    choice = provider_env.subscription_provider(
        ca_path="/ca.pem", project_id="p", topic_id="t"
    )
    assert not [k for k in choice.env if "MODEL" in k]


def test_no_credential_file_is_ever_planted_in_the_box(tmp_path):
    """The session's login is its host's own store; nothing credential-shaped is
    planted into its config dir, where a private copy would be stranded by the
    first sibling's refresh."""
    import subprocess

    from app.domain.agent.harness.claude_code.device_launch import launch_holes

    holes = launch_holes(state="$HOME/.cheese/harness/p/r/claude-code/x")
    subprocess.run(
        ["sh", "-c", "set -e\n" + holes.configure],
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    planted = {path.name for path in (tmp_path / ".claude").rglob("*")}
    assert "settings.json" in planted
    assert ".credentials.json" not in planted
    # What it DOES plant is not credential-shaped.
    settings = (tmp_path / ".claude/settings.json").read_text()
    assert "token" not in settings.lower()
