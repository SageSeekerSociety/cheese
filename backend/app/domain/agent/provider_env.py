"""How a machine reaches a model, expressed as environment.

There is one shape (结论 46). Claude Code keeps ``ANTHROPIC_BASE_URL`` unset and
travels through the metering proxy on ``HTTPS_PROXY``; the proxy asks the
backend per request whether that request goes to the subscription pool or is
rewritten to the API-key gateway.

Pointing ``ANTHROPIC_BASE_URL`` at a gateway instead would re-originate the call
from our own HTTP client: a different user agent, header shape and rhythm — a
fingerprint the provider can act on, and on the subscription the account at risk
is a person's. So the transport does not vary with the pool, and the pool is not
a property of the machine at all.

The proxy streams model bodies, replaces authentication headers and writes the
resolved model name into the request body. RC-enabled sessions additionally
route control traffic to Cheese; see docs/remote-control.md.
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


def subscription_provider(
    *,
    ca_path: str,
    project_id: str | None = None,
    topic_id: str | None = None,
    session_token: str | None = None,
    connect_proxy_url: str | None = None,
    no_proxy: str | None = None,
) -> ProviderChoice:
    """The subscription, as the container env for a metered sandbox.

    The machine holds NO real credential (hard requirement — a leaked machine
    credential is a leaked subscription). It ships a fake one, and every request
    is redirected to the metering proxy, which rewrites the Authorization to the
    real token — that token lives only on the backend. So the machine's env only
    has to:

      - trust the proxy's CA (it terminates TLS for api.anthropic.com);
      - NOT carry a stale gateway key — blank, not absent, or the CLI inherits
        the backend's key and silently drops to API-key mode;
      - announce which project/topic to bill.

    Login is established by CLAUDE_CODE_OAUTH_TOKEN: interactive Claude Code takes
    an OAuth token from the env as "logged in" without the local validation it
    applies to a .credentials.json file, and sends it as the Bearer — the proxy
    swaps it for the real token.

    ``session_token`` is what that Bearer carries. When the metering proxy is
    reachable only on the box's own docker bridge, a fixed placeholder is enough
    (nothing else can reach the proxy). The moment the proxy is exposed to a
    machine network, the placeholder — which is public in this repo — would let
    anyone reach through it and spend the subscription; so a real, per-session
    **scoped cheese token** (HMAC over {project, topic, exp}) is passed instead.
    The proxy verifies its signature before injecting the real token and takes
    the billing attribution from its claims, not from the spoofable
    ``x-cheese-attr`` header. A scoped token is not a subscription credential —
    it authenticates ONLY "ask the proxy to bill this project", so the hard
    "no real credential on the machine" rule still holds. Absent, it falls back
    to the placeholder (unchanged single-host behaviour).

    Crucially it sets NO ``ANTHROPIC_BASE_URL``. Setting one puts interactive
    Claude Code into "API Usage Billing" mode — it treats the endpoint as a
    custom API needing a key, ignores the OAuth token and shows "Not logged in".
    Leaving it unset keeps it in SUBSCRIPTION mode against api.anthropic.com;
    only the TRANSPORT differs by machine shape:

      - a container is steered by ``--add-host`` (to the proxy on 443) — DNS-level
        capture, which caught undici back when the node-built CLI ignored proxy
        env vars entirely;
      - a bare DEVICE process (no root, no docker, no /etc/hosts write) is steered
        by ``connect_proxy_url`` → ``HTTPS_PROXY``, pointing at the proxy's
        CONNECT listener. The current CLI is the native build on every install
        route (the npm package now wraps the same binary) and it sends
        /v1/messages through HTTPS_PROXY — measured 2026-08-13 on 2.1.229/2.1.231
        with a logging CONNECT proxy: the model calls CONNECT through it, the
        turn answers, and Proxy-Authorization carries the URL's userinfo. So the
        scoped session token doubles as the CONNECT credential
        (``http://cheese:<token>@host:port``), which is what keeps an exposed
        CONNECT listener from being an open relay.

    ``no_proxy`` (→ ``NO_PROXY``/``no_proxy``) must name the backend and loopback:
    the CLI routes even plain-http requests through HTTPS_PROXY, so without it the
    hooks/git/CLI traffic detours through the meter — or dies with it.

    The CLI generates the model request; proxy authentication and RC routing
    are separate. The model is deliberately NOT pinned: the subscription serves its
    own (claude-opus-5), and forcing a name it does not serve fails the turn.
    """
    env = {
        # Establishes "logged in" AND is what the CLI sends as the Bearer — the
        # proxy verifies it, then swaps it for the real token.
        "CLAUDE_CODE_OAUTH_TOKEN": session_token or SUBSCRIPTION_PLACEHOLDER_TOKEN,
        # Blank, not absent: an inherited ANTHROPIC_AUTH_TOKEN would flip the CLI
        # into API-key mode and bypass the OAuth path.
        "ANTHROPIC_AUTH_TOKEN": "",
        "NODE_EXTRA_CA_CERTS": ca_path,
    }
    if connect_proxy_url:
        env["HTTPS_PROXY"] = connect_proxy_url
    if no_proxy:
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy
    if project_id:
        attr = f"{project_id}/{topic_id}" if topic_id else project_id
        env["ANTHROPIC_CUSTOM_HEADERS"] = f"x-cheese-attr: {attr}"
    return ProviderChoice(name="subscription", env=env)


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
