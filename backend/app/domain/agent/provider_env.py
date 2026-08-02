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
    """Zhipu / DeepSeek and anything else reached with a key we hold.

    ``key`` must be the machine's own scoped cheese token, never the upstream
    provider key, and ``gateway_base`` the backend's /api/llm route rather than
    the gateway itself. The backend swaps in the project's virtual key on the
    way through.

    A machine used to receive the raw provider key in its environment, in plain
    sight of anyone on that host — it was visible in a tmux command line — and
    its spend landed in the invoice under one undifferentiated key. Keeping the
    credential on the box makes attribution structural rather than a promise the
    machine has to keep.
    """
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


def subscription_provider(
    proxy_url: str,
    ca_path: str,
    *,
    project_id: str | None = None,
    topic_id: str | None = None,
    base_url: str | None = None,
) -> ProviderChoice:
    """The subscription, reached through a transparent proxy.

    The model names are deliberately ABSENT: overriding them would make Claude
    Code ask the official API for a model it does not serve.

    ``HTTPS_PROXY`` alone does NOT capture the turn. Measured on this
    deployment: with the proxy set, the meter saw `oauth/profile`,
    `mcp_servers` and `eval/sdk` — and never a single `/v1/messages`, while
    every turn still answered. Claude Code issues the model call through Node's
    built-in undici, which ignores the proxy env vars (`NODE_USE_ENV_PROXY` is
    Node 24+; this runs on 20, and the CLI ships as a compiled binary). A meter
    wired only to HTTPS_PROXY therefore bills nothing while reporting success —
    the worst possible failure for an accounting path.

    So the capture is done by NAME instead: the sandbox resolves
    api.anthropic.com to our proxy (``--add-host``, see TmuxHooksProvider), which
    catches undici too because it goes through DNS. ``base_url`` only carries the
    port when the proxy cannot listen on 443; the host stays api.anthropic.com so
    the request upstream is unchanged.

    ``project_id``/``topic_id`` ride along as a custom header (verified to reach
    the proxy) — without it the meter sees tokens it cannot attribute to anyone.
    """
    env = {
        # Kept for the OAuth/refresh traffic, which DOES honour it — and which
        # only succeeds through a proxy (a direct refresh fails on this network).
        "HTTPS_PROXY": proxy_url,
        "HTTP_PROXY": proxy_url,
        # Node's own trust store flag — the proxy terminates TLS, so its CA
        # has to be trusted by the client that actually makes the call.
        "NODE_EXTRA_CA_CERTS": ca_path,
        # Blank, not absent: the CLI inherits the backend's environment, and a
        # leaked API-key token would silently switch it out of subscription mode.
        "ANTHROPIC_AUTH_TOKEN": "",
    }
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    if project_id:
        attr = f"{project_id}/{topic_id}" if topic_id else project_id
        env["ANTHROPIC_CUSTOM_HEADERS"] = f"x-cheese-attr: {attr}"
    return ProviderChoice(name="subscription", env=env)


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


@dataclass(frozen=True)
class ContainerSubscription:
    """What a CONTAINERISED agent needs before the subscription works inside it.

    The local backend runs Claude Code in a container, and the subscription's
    settings bake the HOST's absolute CA path into NODE_EXTRA_CA_CERTS. So the
    container cannot simply be handed the file — it has to see it at the SAME
    absolute path, which means mounting the host's `.claude` there and giving
    the container the same HOME. Copying to a different path silently produces a
    Claude that cannot verify the proxy, which fails as a TLS error far from its
    cause.

    Host and container then share one ccproxy identity, so the engine bills and
    switches them as a single machine — which is the desired behaviour, not an
    accident to be worked around.

    Switching is NOT hot: env is read once at process start, so a channel change
    on the host only reaches a container after its Claude process is restarted.
    Same as on the host; the difference is that nothing in a container reminds
    you.
    """

    host_claude_dir: str
    mount: str  # "src:dst" — identical by construction, see below
    home: str


def container_subscription(host_claude_dir: str) -> ContainerSubscription:
    """Bind mount + HOME for a container that must use the host's subscription."""
    d = host_claude_dir.rstrip("/")
    return ContainerSubscription(
        host_claude_dir=d,
        # Same path on both sides. Expressed as one variable rather than two so
        # the invariant cannot drift: a mount whose destination differs from its
        # source is the failure this whole type exists to prevent.
        mount=f"{d}:{d}",
        home=str(pathlib_parent(d)),
    )


def pathlib_parent(path: str) -> str:
    """The HOME that contains a `.claude` directory."""
    return path.rsplit("/", 1)[0] or "/"
