"""mitmproxy addon: meter every Claude turn, cap it, and get it authenticated.

This sits between a caller's Claude Code and the upstream. The caller MUST NOT
hold a credential that is spendable on its own (hard requirement: a machine that
leaks an auth key is a machine that leaks the subscription). There are two ways
to satisfy that, and this addon serves both:

  PASS THROUGH — the caller is an enrolled machine carrying a ccproxy ticket
    issued to its own identity. That ticket is not spendable: ccproxy swaps it
    for the real credential at its own edge. So the bearer is forwarded
    untouched and this host holds nothing. It works only because the upstream
    hop is authenticated as that same machine (http_connect_upstream) — ccproxy
    scopes the swap to the authenticated connection.

  SWAP — nothing places the caller on a machine identity (the local container
    path; a machine enrolled before identities were recorded). The caller ships
    a scoped cheese token, enough to make Claude Code believe it is logged in
    and to authenticate "bill this project", and this proxy rewrites the
    Authorization header to the credential the HOST holds. That credential never
    touches the caller's disk or environment.

That placement also makes this the only point that can:
  - meter a subscription turn's real cost (the subscription path deliberately
    skips LiteLLM — no per-call key to meter, and re-originating from our own
    client would change what the provider sees),
  - enforce a cap BEFORE forwarding, so an exhausted budget cannot overspend:
    per-project via the backend's /llm/admission (#218), plus the rolling
    token window as the deployment-wide backstop,
  - hold, for the SWAP path only, the one durable credential those callers never
    see: a non-refreshing one-year `claude setup-token` (or a stable ccproxy
    ticket). There is no refresh loop and no daemon — a setup-token does not
    rotate — so no two callers can race a rotation and kill it. Rotation is a
    planned, roughly annual manual swap of the token file, not a background
    process.

Attribution comes from the VERIFIED claims of the caller's scoped token (#198)
when CHEESE_SCOPED_SECRET is set; the legacy x-cheese-attr header is honored
only when CHEESE_ALLOW_HEADER_ATTR=1 (bridge-only deployments still on the
fixed placeholder token). On a proxy exposed beyond the box's own docker
bridge, leave that off — the header is whatever the machine says it is.

  mitmdump -s billing_addon.py \
    --mode reverse:https://api.anthropic.com@8443 --mode regular@8444

Two listeners, one addon: containers arrive on the reverse listener (steered by
--add-host on 443), bare DEVICE screens on the regular one (steered by
HTTPS_PROXY — no root, no docker, so no --add-host for them). The regular
listener demands the scoped token as Proxy-Authorization before it relays
anything and MITMs only the Anthropic names; either way every request that
reaches the `request` hook below is handled identically.

Config (env): CHEESE_USAGE_LOG, CHEESE_INJECT_TOKEN, CHEESE_TOKEN_CAP,
CHEESE_CAP_WINDOW_S, CHEESE_UPSTREAM_VIA, CHEESE_SCOPED_SECRET,
CHEESE_ALLOW_HEADER_ATTR, CHEESE_ADMISSION_URL, CHEESE_ADMISSION_CACHE_S,
CHEESE_UPSTREAM_AUTH.
"""

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from mitmproxy import http, tls

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cheese_billing_core import (  # noqa: E402
    ANTHROPIC_HOSTS,
    GATEWAY,
    AdmissionGate,
    Meter,
    proxy_basic_password,
    usage_from_sse,
    verify_scoped_token,
)

logger = logging.getLogger("cheese.metering")

X_ATTR_HEADER = "x-cheese-attr"  # legacy "<project_id>/<topic_id>", spoofable
USAGE_LOG = Path(os.environ.get("CHEESE_USAGE_LOG", "/var/log/cheese/usage.jsonl"))
# 0 disables the backstop cap. Set per deployment from the subscription's ceiling.
TOKEN_CAP = int(os.environ.get("CHEESE_TOKEN_CAP", "0"))
CAP_WINDOW_S = int(os.environ.get("CHEESE_CAP_WINDOW_S", str(5 * 3600)))
# File holding the REAL durable credential (just the token string, no JSON): a
# non-refreshing one-year `claude setup-token`, or the stable ccproxy fake
# token. Nothing writes it at runtime — a setup-token does not rotate, so there
# is no refresh loop and no daemon; rotation is a planned manual swap. The addon
# re-reads the file on every request, so a manual atomic replace inside the
# mounted secrets dir is picked up without a proxy restart. Absent/empty = fail
# closed with a local 503 (see request()), never a silent forward of the
# sandbox's own scoped token upstream.
INJECT_TOKEN_FILE = Path(
    os.environ.get("CHEESE_INJECT_TOKEN", "/etc/cheese/secrets/inject.token")
)
# Shared with the backend (SANDBOX_TOKEN): verifies the caller's scoped token.
SCOPED_SECRET = os.environ.get("CHEESE_SCOPED_SECRET", "")
ALLOW_HEADER_ATTR = os.environ.get("CHEESE_ALLOW_HEADER_ATTR", "") == "1"
ADMISSION_URL = os.environ.get("CHEESE_ADMISSION_URL", "")
ADMISSION_CACHE_S = float(os.environ.get("CHEESE_ADMISSION_CACHE_S", "30"))

# Route the upstream through the EXPLICIT ccproxy (m161): measured, the OAuth
# token is only accepted on that path — going direct to api.anthropic.com (the
# ghg transparent layer) returns "OAuth access token is invalid", while the same
# token through m161 gets a real request_id. reverse mode does not honour
# HTTPS_PROXY for its upstream, so the route is set per-flow via server_conn.via.
UPSTREAM_VIA = os.environ.get("CHEESE_UPSTREAM_VIA", "")  # "host:port"

# Which ccproxy identity to authenticate that hop as, `user:password`. This used
# to be mitmdump's own --upstream-auth, which stamps ONE identity onto every
# upstream connection. It cannot stay global: ccproxy scopes its fake->real
# ticket swap to the authenticated connection, so a machine's own ticket is only
# honoured over that machine's identity (measured 2026-08-14 — m516's ticket over
# an m161 connection returns 401 with no request_id, the same ticket over m516's
# own connection reaches Anthropic). The per-request identity therefore comes
# from the admission verdict, and this env is only the fallback for traffic the
# control plane cannot place: machines enrolled before the identity was recorded,
# and the local container path.
UPSTREAM_AUTH = os.environ.get("CHEESE_UPSTREAM_AUTH", "")  # "user:password"

# Where the API-key pool lives (LiteLLM). A project whose supply decision says
# `gateway` is rewritten to this base instead of going out through ccproxy —
# same interception point, different destination (#243). Empty = this
# deployment has no API-key pool, and such a project is refused rather than
# silently served from the subscription it did not ask for.
GATEWAY_BASE = os.environ.get("CHEESE_GATEWAY_BASE", "")  # "http://host:port"

METER = Meter(USAGE_LOG, CAP_WINDOW_S)
ADMISSION = AdmissionGate(ADMISSION_URL, cache_s=ADMISSION_CACHE_S)

if not ADMISSION_URL:
    # Said once, loudly, at load: an unset env var produces no error anywhere
    # downstream, and both things it switches off are invisible from outside —
    # the budget never refuses, and no enrolled machine is ever placed on its
    # own identity. A deployment that means it can read this line and move on.
    logger.warning(
        "CHEESE_ADMISSION_URL is unset: per-project budgets are NOT enforced "
        "(only the rolling token cap), and no enrolled machine can be placed on "
        "its own ccproxy identity — every such turn will be refused instead of "
        "falling back to the platform credential."
    )

# Which identity each client connection's traffic goes out as, keyed by the
# client connection's id. Two hooks have to agree and neither can tell the other
# directly: `request` learns the identity from the admission verdict, while the
# upstream CONNECT is a DIFFERENT flow raised later, when the lazy server
# connection is finally opened. The client connection is what they share.
_UPSTREAM_BY_CLIENT: dict[str, str] = {}

# What each client connection PROVED at CONNECT time: (scoped token, claims).
# The pass-through path needs this because its two halves otherwise contradict
# each other — relaying a machine's own ccproxy ticket means the request Bearer
# is that ticket, while attribution wants a scoped cheese token in the very same
# header. Only one can be there. The scoped token is not missing though: the
# tunnel helper stamps it as the CONNECT's proxy password, which http_connect
# already verifies to open the tunnel at all. Keeping the verified result turns
# that check into a second source of attribution instead of a fact thrown away.
_SCOPED_BY_CLIENT: dict[str, tuple[str, dict]] = {}


def _real_token() -> str:
    try:
        return INJECT_TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def _via() -> tuple[str, tuple[str, int]] | None:
    if not UPSTREAM_VIA:
        return None
    host, _, port = UPSTREAM_VIA.rpartition(":")
    return ("http", (host, int(port)))


def _caller_bearer(flow: http.HTTPFlow) -> str:
    auth = flow.request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _topic_within(flow: http.HTTPFlow, project: str, claims: dict) -> str:
    """The topic to bill inside an ALREADY PROVEN project.

    One machine hosts several topics of a project but shares a single tunnel
    helper, and that helper holds one token file — rewritten by whichever
    session launched last. So the CONNECT's token names the right project and an
    arbitrary one of its topics. The per-request header is the only per-topic
    signal that survives to here.

    Honouring it for the topic alone is safe in a way honouring it for the
    project is not. The project decides who pays and which budget is checked, so
    it stays strictly on verified claims. The topic only says which of that
    payer's own rows this lands on; a caller lying about it can misattribute a
    turn inside a project it already proved it owns, which costs no one else
    anything. Hence the equality check: a header naming a different project is
    discarded whole, never used to move billing.
    """
    attr = flow.request.headers.get(X_ATTR_HEADER, "")
    header_project, _, header_topic = attr.partition("/")
    if header_topic and header_project == project:
        return header_topic
    return str(claims.get("t") or "")


def _attribution(flow: http.HTTPFlow) -> tuple[str, str, str, bool]:
    """(project_id, topic_id, scoped_token, carries_own_credential) for this
    request.

    Verified claims first; the spoofable header only where explicitly allowed.
    Empty project = unattributable (recorded as such; refused separately when
    scoped auth is required).

    Two places carry verified claims, and the second is not a fallback for a
    weaker caller — it is the ONLY one an enrolled machine can use. Such a
    caller's Bearer is its own ccproxy ticket by design (that is what
    pass-through relays), so its scoped token can only be the one it proved when
    it opened the tunnel.

    The third element is what authenticates the admission call, so it must be
    the scoped token and not merely whatever was in the Authorization header —
    handing admission a ccproxy ticket 401s, and the proxy fails OPEN on
    admission errors, which would leave the budget brake silently not braking.

    The fourth element says the caller's Authorization header is a credential of
    its OWN rather than a scoped cheese token — true exactly when the claims
    came from the CONNECT. Only a machine relaying its own ccproxy ticket looks
    like that, and it is the one caller whose header must never be overwritten
    with the platform's credential (see request()).
    """
    bearer = _caller_bearer(flow)
    if SCOPED_SECRET:
        claims = verify_scoped_token(bearer, SCOPED_SECRET)
        if claims:
            return str(claims.get("p") or ""), str(claims.get("t") or ""), bearer, False
        pinned = _SCOPED_BY_CLIENT.get(getattr(flow.client_conn, "id", ""))
        if pinned:
            token, connect_claims = pinned
            project = str(connect_claims.get("p") or "")
            return project, _topic_within(flow, project, connect_claims), token, True
    if ALLOW_HEADER_ATTR:
        attr = flow.request.headers.get(X_ATTR_HEADER, "")
        project, _, topic = attr.partition("/")
        return project, topic, bearer, False
    return "", "", bearer, False


def _route_to_gateway(flow: http.HTTPFlow, key: str) -> bool:
    """Send this request to the API-key pool (LiteLLM) instead of ccproxy.

    Returns False when the deployment has no gateway configured or the control
    plane could not supply the project's virtual key — the caller refuses in
    that case. Serving such a request from the subscription instead would bill
    a pool the project did not choose, which is the exact confusion this whole
    routing decision exists to remove.

    The upstream hop is direct: `via` is cleared because ccproxy's egress is
    only needed for Anthropic, and sending Zhipu/DeepSeek traffic through it
    would route domestic providers out through an overseas exit for no reason.
    """
    if not GATEWAY_BASE or not key:
        return False
    parsed = urlparse(GATEWAY_BASE)
    if not parsed.hostname:
        return False
    flow.server_conn.via = None
    flow.request.scheme = parsed.scheme or "http"
    flow.request.host = parsed.hostname
    flow.request.port = parsed.port or (443 if flow.request.scheme == "https" else 80)
    flow.request.headers["host"] = parsed.netloc
    # LiteLLM authenticates with the project's virtual key; the subscription
    # credential must NOT ride along (x-api-key would also override the bearer
    # downstream, which is how a swapped credential silently 401s).
    flow.request.headers["authorization"] = f"Bearer {key}"
    flow.request.headers.pop("x-api-key", None)
    return True


def _refuse(flow: http.HTTPFlow, status: int, kind: str, message: str) -> None:
    flow.response = http.Response.make(
        status,
        json.dumps(
            {"type": "error", "error": {"type": kind, "message": message}}
        ).encode(),
        {"Content-Type": "application/json"},
    )


def http_connect(flow: http.HTTPFlow) -> None:
    """Gate the CONNECT (regular-mode) listener: a bare DEVICE screen reaches the
    meter via HTTPS_PROXY, and its scoped cheese token rides as the proxy
    password (Basic userinfo of the HTTPS_PROXY URL). Without this gate an
    exposed listener is an open relay for whoever can reach it — with it, only a
    caller that can prove "bill this project" gets a tunnel at all. Reverse-mode
    connections never CONNECT, so the container path is untouched.

    Fails CLOSED when no secret is configured, rather than falling back to the
    old bridge-only trust model. The listener's bind address is now a per-box
    setting (CONNECT_BIND_HOST, so MicroCloud machines can reach it), and a
    deployment that widens the bind without setting CHEESE_SCOPED_SECRET would
    otherwise turn the meter into an open relay — silently, since nothing about
    a missing env var looks like a failure. The one documented exception stays
    explicit: CHEESE_ALLOW_HEADER_ATTR=1, which already means "this box trusts
    whoever can reach it"."""
    if ALLOW_HEADER_ATTR:
        return
    password = proxy_basic_password(flow.request.headers.get("proxy-authorization", ""))
    claims = (
        verify_scoped_token(password, SCOPED_SECRET)
        if password and SCOPED_SECRET
        else None
    )
    if claims:
        # Kept, not discarded: for an enrolled machine this is the only scoped
        # token on the whole connection — its request Bearer is the ccproxy
        # ticket that pass-through exists to relay. See _attribution.
        _SCOPED_BY_CLIENT[getattr(flow.client_conn, "id", "")] = (password, claims)
        return
    flow.response = http.Response.make(
        407,
        b"cheese: a valid scoped token is required as the proxy password",
        {"Proxy-Authenticate": 'Basic realm="cheese-metering"'},
    )


def http_connect_upstream(flow: http.HTTPFlow) -> None:
    """Authenticate the ccproxy hop as the MACHINE whose traffic this carries.

    mitmproxy's own upstream_auth addon does this from a single --upstream-auth
    option; that option is deliberately NOT passed any more, so this is the only
    writer of the header and there is no ordering race between two addons over
    the same value.

    Falls back to the deployment-wide identity for any connection the control
    plane could not place. That fallback is not merely a default: the request
    hook only forwards a caller's own ticket when it HAS a per-machine identity,
    and swaps in the platform credential otherwise — so the two always agree
    about which identity the ticket belongs to.
    """
    auth = _UPSTREAM_BY_CLIENT.get(getattr(flow.client_conn, "id", "")) or UPSTREAM_AUTH
    if auth:
        encoded = base64.b64encode(auth.encode()).decode()
        flow.request.headers["Proxy-Authorization"] = f"Basic {encoded}"


def client_disconnected(client) -> None:
    """A long-lived proxy must not accumulate one entry per connection ever
    made; neither the identity nor the proven project outlives the connection
    that established it — and a recycled connection id must not inherit the
    previous caller's project."""
    _UPSTREAM_BY_CLIENT.pop(getattr(client, "id", ""), None)
    _SCOPED_BY_CLIENT.pop(getattr(client, "id", ""), None)


def tls_clienthello(data: tls.ClientHelloData) -> None:
    """On the CONNECT listener, MITM ONLY the Anthropic names. Everything else a
    device's HTTPS_PROXY sends here (its shell tools honor the env var too:
    pip, statsig, github…) tunnels raw — TLS stays end-to-end, so tools that do
    not trust our CA keep working; they just detour. Reverse-mode connections
    (the container path) are left exactly as they were."""
    mode = getattr(data.context.client, "proxy_mode", None)
    if getattr(mode, "type_name", "") != "regular":
        return
    if (data.client_hello.sni or "") not in ANTHROPIC_HOSTS:
        data.ignore_connection = True


async def request(flow: http.HTTPFlow) -> None:
    # Multi-host by SNI: the sandbox --add-hosts api.anthropic.com AND the login
    # hosts (console.anthropic.com, platform.claude.com) to this one proxy, so
    # interactive Claude Code's login/refresh also gets the real token injected.
    # Reverse mode would pin every request to api.anthropic.com; instead forward
    # each to the host it was actually for, read from the TLS SNI.
    sni = getattr(flow.client_conn, "sni", None)
    if sni:
        flow.request.host = sni
        flow.request.headers["host"] = sni

    # Only the Anthropic names are served, and only over TLS the proxy
    # terminated. This request handler injects the REAL credential below — so a
    # caller naming any other host (an arbitrary SNI on the reverse listener, a
    # plain-HTTP proxy request on the CONNECT one) must be refused, not
    # forwarded: forwarding would hand the subscription token to whatever host
    # the caller chose. Non-Anthropic HTTPS through the CONNECT listener never
    # reaches here (tls_clienthello tunnels it raw).
    if flow.request.host not in ANTHROPIC_HOSTS or not flow.client_conn.tls_established:
        _refuse(
            flow,
            403,
            "invalid_request_error",
            "cheese: only the Anthropic endpoints are served here",
        )
        return

    via = _via()
    if via is not None:
        flow.server_conn.via = via

    # Attribution BEFORE anything else: the caller's own Bearer is the scoped
    # token.
    project_id, topic_id, bearer, carries_own_credential = _attribution(flow)
    flow.metadata["cheese_attr"] = (project_id, topic_id)

    is_messages = "/v1/messages" in flow.request.path

    # Asked for EVERY request, not only /v1/messages. The upstream connection is
    # opened by whichever request comes first, and Claude Code's startup
    # api/oauth/profile check beats the first turn to it — so the identity that
    # connection authenticates as has to be settled by then, or the turn's own
    # ticket goes out over the wrong one. Cheap: verdicts are cached per project.
    verdict = None
    if project_id and ADMISSION_URL:
        # Off-loop: urllib blocks, and one slow admission call must not stall
        # every other flow through the proxy.
        verdict = await asyncio.to_thread(ADMISSION.check, project_id, bearer)

    if is_messages:
        if SCOPED_SECRET and not ALLOW_HEADER_ATTR and not project_id:
            # #198: an exposed proxy must not spend the subscription for a
            # caller that cannot prove which project to bill.
            _refuse(
                flow,
                401,
                "authentication_error",
                "cheese: a valid scoped token is required",
            )
            return
        if verdict is not None:
            if not verdict.allow:
                _refuse(
                    flow,
                    429,
                    "rate_limit_error",
                    f"cheese project budget: {verdict.reason}",
                )
                return
            # Supply decision (#243): the same answer says WHERE this project's
            # traffic goes. The subscription is the default and keeps every
            # step below (ccproxy egress, the rolling cap); a gateway project
            # leaves here and none of it applies.
            if verdict.pool == GATEWAY:
                if not _route_to_gateway(flow, verdict.key or ""):
                    _refuse(
                        flow,
                        503,
                        "api_error",
                        "cheese: this project is configured for the API-key "
                        "pool, but the pool has no route or no project key on "
                        "this deployment; no model call was made",
                    )
                return
        if METER.would_exceed(TOKEN_CAP):
            used = METER.used()
            _refuse(
                flow,
                429,
                "rate_limit_error",
                f"cheese subscription cap reached: {used}/{TOKEN_CAP} tokens "
                f"in the last {CAP_WINDOW_S // 3600}h",
            )
            return

    # A stale x-api-key would override whatever bearer goes upstream, on either
    # path below — so it is dropped before the branch, not inside one.
    flow.request.headers.pop("x-api-key", None)

    # PASS THROUGH. The caller is a machine whose own ccproxy ticket we can
    # relay, because http_connect_upstream will authenticate this connection as
    # that same machine. Its ticket is not a credential we could spend anyway —
    # ccproxy swaps it for the real one at its own edge — so the platform holds
    # no model credential for this path at all.
    if verdict is not None and verdict.upstream:
        _UPSTREAM_BY_CLIENT[getattr(flow.client_conn, "id", "")] = verdict.upstream
        return

    # Reaching here with a caller that brought its OWN credential means the
    # control plane could not place it, and the swap below would replace that
    # caller's ticket with the platform's — spending the wrong account, and
    # reporting it as an auth failure that reads like the caller's own.
    #
    # This is the failure that cost a day (2026-08-15): the box had no
    # CHEESE_ADMISSION_URL, so no verdict ever carried an identity, so every
    # enrolled machine silently fell back to the platform credential and every
    # turn died with an upstream "OAuth access token has been revoked" naming a
    # token the machine never held. Nothing anywhere said "admission is not
    # configured" — an unset env var looks exactly like a working one. Refusing
    # here turns that into one line that names the missing piece.
    if carries_own_credential:
        _refuse(
            flow,
            503,
            "api_error",
            "cheese: this machine carries its own ccproxy ticket but the "
            "control plane did not say which identity to send it as — "
            "CHEESE_ADMISSION_URL unset, admission unreachable, or the machine "
            "has no recorded ccproxy identity; refusing rather than spending "
            "the platform's credential",
        )
        return

    # SWAP. Nothing placed this caller on a machine identity, so its bearer is a
    # scoped cheese token that means nothing upstream, and the hop goes out on
    # the deployment-wide identity — whose ticket is the one this host holds.
    #
    # On EVERY request, not just messages: Claude Code validates its login
    # against api/oauth/profile at startup, so if only /v1/messages carried the
    # real token that check would 401 and the turn would never start.
    token = _real_token()
    # Fail closed BEFORE forwarding when the platform has no credential of its
    # own either. Return a clear local 503 so the caller learns the PLATFORM
    # credential is the problem, instead of forwarding the sandbox's scoped
    # bearer upstream only to collect an opaque 401 that reads like the caller's
    # own auth failing. The fix is host-side: (re)install the durable
    # setup-token in the secrets dir.
    if not token:
        _refuse(
            flow,
            503,
            "api_error",
            "cheese: subscription credential unavailable — the platform's "
            "Claude setup-token is missing or expired; host-side configuration "
            "is required before requests can be served",
        )
        return
    flow.request.headers["authorization"] = f"Bearer {token}"


def response(flow: http.HTTPFlow) -> None:
    if "/v1/messages" not in flow.request.path or not flow.response:
        return
    if flow.response.status_code != 200:
        return
    project_id, topic_id = flow.metadata.get("cheese_attr") or ("", "")
    body = flow.response.raw_content or b""
    ctype = flow.response.headers.get("content-type", "")
    if "event-stream" in ctype:
        usage, model = usage_from_sse(body)
    else:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return
        usage, model = payload.get("usage", {}), payload.get("model", "")
    if usage:
        METER.record(project_id, topic_id, usage, model)
