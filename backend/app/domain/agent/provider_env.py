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

# The placeholder the container carries in CLAUDE_CODE_OAUTH_TOKEN. Shaped like a
# real OAuth token but an obvious non-credential: interactive Claude Code accepts
# an OAuth token from the env WITHOUT the local validation it applies to a
# .credentials.json file (measured — the file path showed "Not logged in", the
# env var showed the normal prompt), and the metering proxy rewrites it to the
# real token on the way out. So the box never holds anything that authenticates.
SUBSCRIPTION_PLACEHOLDER_TOKEN = (
    "sk-ant-oat01-cheese-placeholder-not-a-real-credential-injected-at-proxy"
)


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
    *,
    ca_path: str,
    project_id: str | None = None,
    topic_id: str | None = None,
) -> ProviderChoice:
    """The subscription, as the container env for a metered sandbox.

    The container holds NO real credential (hard requirement — a leaked machine
    credential is a leaked subscription). It ships a fake one, and every request
    is redirected BY NAME to the metering proxy (``--add-host`` on 443, see
    TmuxHooksProvider), which rewrites the Authorization to the real token — that
    token lives only on the backend. So the container env only has to:

      - trust the proxy's CA (it terminates TLS for api.anthropic.com);
      - NOT carry a stale gateway key — blank, not absent, or the CLI inherits
        the backend's key and silently drops to API-key mode;
      - announce which project/topic to bill.

    Login is established by CLAUDE_CODE_OAUTH_TOKEN (a placeholder): interactive
    Claude Code takes an OAuth token from the env as "logged in" without the local
    validation it applies to a .credentials.json file, which rejected the same
    placeholder as "Not logged in". The proxy rewrites it to the real token.

    Crucially it sets NO ``ANTHROPIC_BASE_URL``. Setting one puts interactive
    Claude Code into "API Usage Billing" mode — it treats the endpoint as a
    custom API needing a key, ignores the OAuth token and shows "Not logged in".
    Leaving it unset keeps it in SUBSCRIPTION mode against api.anthropic.com;
    ``--add-host`` alone (to the proxy on 443) does the routing, so the request
    is byte-for-byte an ordinary session and capture still catches undici (DNS).

    The model is deliberately NOT pinned: the subscription serves its own
    (claude-opus-5), and forcing a name it does not serve fails the turn.
    """
    env = {
        # Establishes "logged in" AND is what the CLI sends as the Bearer — the
        # proxy swaps it for the real token. A non-credential on its own.
        "CLAUDE_CODE_OAUTH_TOKEN": SUBSCRIPTION_PLACEHOLDER_TOKEN,
        # Blank, not absent: an inherited ANTHROPIC_AUTH_TOKEN would flip the CLI
        # into API-key mode and bypass the OAuth path.
        "ANTHROPIC_AUTH_TOKEN": "",
        "NODE_EXTRA_CA_CERTS": ca_path,
    }
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
    ca_path: str,
) -> ProviderChoice:
    """Pick one. Falling back from the subscription to a key-based provider is
    intentional and safe — the reverse never happens implicitly, because sending
    subscription traffic through our own client is the thing being avoided."""
    if prefer_subscription and ca_path:
        return subscription_provider(ca_path=ca_path)
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
