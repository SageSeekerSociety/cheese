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
    proxy_url="http://ccproxy:3128",
    ca_path="/ca.crt",
)


def test_the_subscription_leaves_the_endpoint_and_model_alone():
    """Overriding either would send the official API a model it does not serve,
    and would mark the traffic as something other than an ordinary session."""
    env = choose(prefer_subscription=True, **_ARGS).env

    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert not [k for k in env if "MODEL" in k], env
    assert env["HTTPS_PROXY"] == "http://ccproxy:3128"
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
    """A half-applied subscription is the dangerous state: proxy set with no CA,
    or a CA with nowhere to send. Falling back to the key provider is safe; the
    reverse must never happen implicitly."""
    for missing in ({"proxy_url": ""}, {"ca_path": ""}):
        env = choose(prefer_subscription=True, **{**_ARGS, **missing}).env
        assert env["ANTHROPIC_BASE_URL"] == "http://gw:4000", missing
        assert "HTTPS_PROXY" not in env, missing
