"""Choosing a provider must not change what the subscription's client looks like.

Routing subscription traffic through our own HTTP client re-originates the
request: different user agent, different header shape, different rhythm. That is
a fingerprint the provider can act on, and the account at risk belongs to a
person. So the two providers are served by two different mechanisms, and the
test that matters is that they stay separate.
"""

from app.domain.agent.provider_env import choose

_ARGS = dict(
    gateway_base="http://gw:4000",
    gateway_key="secret",
    model="glm-5.2",
    ca_path="/ca.crt",
)


def test_the_subscription_sets_no_base_url_and_pins_no_model():
    """Setting a BASE_URL flips interactive Claude Code into API-key mode; the
    subscription must stay OAuth, so it sets none. --add-host does the routing."""
    env = choose(prefer_subscription=True, **_ARGS).env

    assert "ANTHROPIC_BASE_URL" not in env
    # Blanked rather than absent: the CLI inherits the backend's os.environ, so
    # an ANTHROPIC_AUTH_TOKEN left over from the gateway path would put it in
    # API-key mode — billing another account and bypassing the meter entirely.
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert not [k for k in env if "MODEL" in k], env
    assert env["NODE_EXTRA_CA_CERTS"] == "/ca.crt"


def test_a_key_provider_is_addressed_directly_and_never_proxied():
    """Our own key through our own gateway — we are a legitimate API client
    there, and pushing it through the subscription's proxy would hand a third
    party traffic that has nothing to do with it."""
    env = choose(prefer_subscription=False, **_ARGS).env

    assert env["ANTHROPIC_BASE_URL"] == "http://gw:4000"
    assert env["CLAUDE_MODEL"] == "glm-5.2"
    assert "HTTPS_PROXY" not in env
    assert "HTTP_PROXY" not in env


def test_an_unconfigured_subscription_falls_back_rather_than_half_applying():
    """A half-applied subscription is the dangerous state: a port with no CA, or
    a CA with nowhere to send. Falling back to the key provider is safe; the
    reverse must never happen implicitly."""
    for missing in ({"ca_path": ""},):
        env = choose(prefer_subscription=True, **{**_ARGS, **missing}).env
        assert env["ANTHROPIC_BASE_URL"] == "http://gw:4000", missing
        assert "HTTPS_PROXY" not in env, missing


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
