"""A machine gets one model environment, and it names no model.

There is no second provider shape to pick between any more: every machine is
launched against the metering proxy, and which pool serves one request — and
which model it runs on — is answered per request at admission. What this module
must keep true is that the environment says neither.
"""

from app.domain.agent.provider_env import subscription_provider


def test_the_launch_environment_sets_no_base_url_and_names_no_model():
    """Setting a BASE_URL flips interactive Claude Code into API-key mode, where
    it ignores the OAuth token; and any model key here would be a second
    declaration of what the card's binding already says."""
    env = subscription_provider(ca_path="/ca.crt").env

    assert "ANTHROPIC_BASE_URL" not in env
    # Blanked rather than absent: the CLI inherits the backend's os.environ, so
    # an ANTHROPIC_AUTH_TOKEN left over from the gateway path would put it in
    # API-key mode — billing another account and bypassing the meter entirely.
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert not [k for k in env if "MODEL" in k], env
    assert env["NODE_EXTRA_CA_CERTS"] == "/ca.crt"


def test_the_container_sees_the_ca_at_the_same_absolute_path():
    """The subscription's settings bake the HOST's absolute CA path into
    NODE_EXTRA_CA_CERTS, so a container that mounts it anywhere else gets a
    Claude that cannot verify the proxy — and it fails as a TLS error far from
    the cause. The mount is built from one variable so the two sides cannot
    drift apart."""
    from app.domain.agent.provider_env import container_subscription

    c = container_subscription("/home/nictheboy/.claude")
    src, dst = c.mount.split(":")

    assert src == dst == "/home/nictheboy/.claude"
    assert c.home == "/home/nictheboy", "HOME must contain that .claude"


def test_a_trailing_slash_does_not_produce_a_different_path():
    """Two spellings of the same directory would mount to two places, which is
    the exact failure this prevents."""
    from app.domain.agent.provider_env import container_subscription

    assert (
        container_subscription("/home/x/.claude/").mount
        == container_subscription("/home/x/.claude").mount
    )
