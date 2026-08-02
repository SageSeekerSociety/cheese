"""Which model provider a turn talks to, expressed as environment.

Claude Code has two independent knobs, and picking a provider means choosing
which one to use — they are not interchangeable:

  ANTHROPIC_BASE_URL   where the request goes
  HTTPS_PROXY          how it gets there

An API-key provider (Zhipu, DeepSeek) is served by pointing BASE_URL at our
gateway. We re-originate the request there, which is fine: against an API key we
*are* a legitimate API client.

A subscription cannot be served that way. Its legitimacy rests on the client
being Claude Code itself, so re-originating the call from our own HTTP client
changes the user agent, the header shape and the request rhythm — a fingerprint
the provider can act on, and the account at risk is a person's. The subscription
therefore keeps BASE_URL untouched and travels through a TRANSPARENT proxy: the
request that arrives upstream is byte-for-byte Claude Code's own.

Both remain observable. The difference is only where the observation sits — a
client we control, or a proxy the traffic passes through.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderChoice:
    """One provider, as the environment a screen is launched with."""

    name: str
    env: dict[str, str]


def api_key_provider(gateway_base: str, key: str, model: str) -> ProviderChoice:
    """Zhipu / DeepSeek and anything else reached with a key we hold."""
    return ProviderChoice(
        name="gateway",
        env={
            "ANTHROPIC_BASE_URL": gateway_base,
            "ANTHROPIC_AUTH_TOKEN": key,
            "CLAUDE_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        },
    )


def subscription_provider(proxy_url: str, ca_path: str) -> ProviderChoice:
    """The subscription, reached through a transparent proxy.

    BASE_URL and the model names are deliberately ABSENT: overriding either
    would make Claude Code ask the official API for a model it does not serve,
    and would mark the traffic as something other than an ordinary session.
    """
    return ProviderChoice(
        name="subscription",
        env={
            "HTTPS_PROXY": proxy_url,
            "HTTP_PROXY": proxy_url,
            # Node's own trust store flag — the proxy terminates TLS, so its CA
            # has to be trusted by the client that actually makes the call.
            "NODE_EXTRA_CA_CERTS": ca_path,
        },
    )


def choose(
    *,
    prefer_subscription: bool,
    gateway_base: str,
    gateway_key: str,
    model: str,
    proxy_url: str,
    ca_path: str,
) -> ProviderChoice:
    """Pick one. Falling back from the subscription to a key-based provider is
    intentional and safe — the reverse never happens implicitly, because sending
    subscription traffic through our own client is the thing being avoided."""
    if prefer_subscription and proxy_url and ca_path:
        return subscription_provider(proxy_url, ca_path)
    return api_key_provider(gateway_base, gateway_key, model)
