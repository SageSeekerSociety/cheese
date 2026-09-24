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

The session authenticates with its host's own Claude login; the proxy forwards
that credential to Anthropic untouched, replaces it with the project's virtual
key on the way to the gateway, and writes the resolved model name into the
request body.
"""

from dataclasses import dataclass


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
    connect_proxy_url: str | None = None,
    no_proxy: str | None = None,
) -> ProviderChoice:
    """The environment that steers a session through the metering proxy.

    The Claude credential is not in here: the launch script logs the session in
    with its host's own credential (see the harness's device launch). This env
    only has to:

      - trust the proxy's CA (it terminates TLS for the Anthropic names);
      - NOT carry a stale gateway key — blank, not absent, or the CLI inherits
        the backend's key and silently drops to API-key mode;
      - route through the proxy: ``connect_proxy_url`` → ``HTTPS_PROXY``,
        pointing at the proxy's CONNECT listener with the session's scoped
        cheese token as the proxy password — which is how the proxy knows the
        project to bill and keeps an exposed listener from being an open relay;
      - announce which project/topic to bill.

    Crucially it sets NO ``ANTHROPIC_BASE_URL``. Setting one puts Claude Code
    into "API Usage Billing" mode — it treats the endpoint as a custom API
    needing a key and ignores the OAuth login.

    ``no_proxy`` (→ ``NO_PROXY``/``no_proxy``) must name the backend and loopback:
    the CLI routes even plain-http requests through HTTPS_PROXY, so without it the
    hooks/git/CLI traffic detours through the meter — or dies with it.

    The model is deliberately NOT pinned: admission writes the bound model into
    each request at the proxy.
    """
    env = {
        # Blank, not absent: an inherited ANTHROPIC_AUTH_TOKEN would flip the CLI
        # into API-key mode and bypass the OAuth login.
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
