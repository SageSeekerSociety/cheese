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
reaches the `requestheaders` hook below is handled identically.

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
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from mitmproxy import http, tls

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cheese_billing_core import (  # noqa: E402
    ANTHROPIC_HOSTS,
    BINDING,
    GATEWAY,
    MODEL_REWRITE_LIMIT,
    TELEMETRY_HOSTS,
    AdmissionGate,
    Meter,
    ModelRewrite,
    StreamingUsageExtractor,
    control_answer,
    is_haiku_name,
    proxy_basic_password,
    requested_model_of,
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
# How large a deferred subagent body may be and still be buffered whole for the
# requested-model read (see the defer branch in `requestheaders`). Real subagent
# turns sit orders of magnitude below this; past it the flow keeps the old
# streamed behaviour, which is a floor, not a failure.
DEFER_BODY_LIMIT = int(os.environ.get("CHEESE_DEFER_BODY_LIMIT", str(8 * 1024 * 1024)))
# Same backend as admission; no second public listener or deployment secret.
RC_BASE = ADMISSION_URL.removesuffix("/llm/admission") if ADMISSION_URL else ""
RC_EXTRA_HOSTS = frozenset({"claude.ai", "cdn.growthbook.io"}) | TELEMETRY_HOSTS
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

# 每个 (project, topic) 的主对话最近一次被改写前体里的 model —— CC 给这个
# 会话起分身时回显的就是它。推迟路径拿它认「继承」（见 `_write_bound_model`
# 和 `request`）。一个房间一条主会话，条目数以房间数计；只防极端失控。
PARENT_MODEL: dict[tuple[str, str], str] = {}
_PARENT_MODEL_CAP = 10000

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

    The fourth element says the caller PUT a credential of its own in the
    Authorization header — it sent a bearer, and that bearer is not a scoped
    cheese token. That is the one header the platform's credential must never
    overwrite (see request()).

    An ABSENT bearer is deliberately not that. Claude Code calls some endpoints
    (`/api/event_logging/v2/batch` among them) with no Authorization at all, and
    treating "no credential" as "someone else's credential" refused telemetry
    for every caller on the CONNECT listener whose project has no machine —
    observed on dev the moment this shipped, as a burst of 503s from a project
    that owns no machine at all.
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
            return (
                project,
                _topic_within(flow, project, connect_claims),
                token,
                bool(bearer),
            )
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
    # LiteLLM records API spend; the subscription ledger must not charge it again.
    flow.metadata["cheese_pool"] = GATEWAY
    flow.metadata["cheese_route_ready"] = time.time()
    return True


def _write_bound_model(
    flow: http.HTTPFlow, model: str, *, keep_haiku: bool = False
) -> None:
    """Put the admitted model name into this request's body, on the way past.

    The one control point ends here: the launch environment names no model, so
    the binding resolved at admission has to reach the request body, which is
    the only place either pool reads a model name from.

    ``keep_haiku`` leaves alone what the CLI addressed to the small, fast family
    — session titles, file-path suggestions, the work it does on its own account
    rather than the turn's. See `ModelRewrite`/`_is_haiku`: on the subscription
    those never carried the binding, and a project bound to opus should not
    start writing its session titles with it.

    Re-framed as chunked because the rewrite changes the body's length and the
    headers have already been decided by the time the first chunk arrives —
    there is no Content-Length that could still be right. The alternative is
    buffering the whole body to recompute it, and a long turn re-POSTs its
    entire grown conversation every time: that is the #654 OOM.
    """
    if not model:
        return
    rewrite = ModelRewrite(model, keep_haiku=keep_haiku)
    flow.request.headers.pop("content-length", None)
    flow.request.headers["transfer-encoding"] = "chunked"
    attr = flow.metadata.get("cheese_attr")
    # keep_haiku 不挡记录：haiku 放行时 rewrite.replaced 是 False，记不下
    # 东西；订阅池主对话的真模型该记照记。
    is_main = attr is not None and not flow.metadata.get("cheese_subagent")

    def write(chunk: bytes) -> bytes:
        out = rewrite.feed(chunk)
        if rewrite.missed and not flow.metadata.get("cheese_model_missed"):
            flow.metadata["cheese_model_missed"] = model
            logger.error(
                "no top-level model member in the first %d bytes of a "
                "/v1/messages body; refusing the turn rather than running it on "
                "the client's own choice instead of %s",
                MODEL_REWRITE_LIMIT,
                model,
            )
        if (
            is_main
            and rewrite.replaced
            and isinstance(rewrite.original, str)
            and not is_haiku_name(rewrite.original)
        ):
            # 主对话刚被改写前体里的 model，就是 CC 给这个会话起分身时会回显
            # 的那个名字。记住它，推迟路径拿它认「继承」，不认就是 2026-09-23
            # 那个事故：准入拿席位配置去比 CC 的内建默认，永远不等，gateway
            # 项目的普通分身全被当成范围外指定拒掉。haiku 类是 CLI 自己的后
            # 台请求（会话标题、路径建议），不是父会话的工作模型，不记。
            if len(PARENT_MODEL) >= _PARENT_MODEL_CAP:
                PARENT_MODEL.clear()
            PARENT_MODEL[attr] = rewrite.original
        return out

    flow.request.stream = write


def _write_bound_model_buffered(flow: http.HTTPFlow, model: str) -> None:
    """`_write_bound_model` for a body that is already whole in RAM.

    Only the deferred subagent path is buffered (it had to read the body to
    learn the requested model), and for it this rewrite is almost always the
    identity — the admission already bound the very model the body names. The
    miss handling matches the streaming path: drop the body, mark the flow,
    and let the response hook deliver the refusal that names why (I27).
    """
    if not model:
        return
    rewrite = ModelRewrite(model)
    out = rewrite.feed(flow.request.content or b"")
    # The second, empty feed is the end-of-stream signal: a body whose head
    # never completed a `model` member only misses at this point.
    out += rewrite.feed(b"")
    if rewrite.missed and not flow.metadata.get("cheese_model_missed"):
        flow.metadata["cheese_model_missed"] = model
        logger.error(
            "no top-level model member in a buffered /v1/messages body; "
            "refusing the turn rather than running it on the client's own "
            "choice instead of %s",
            model,
        )
    flow.request.content = out


def _refuse(flow: http.HTTPFlow, status: int, kind: str, message: str) -> None:
    """Answer this request here, and take back the streaming decision.

    Setting a response and streaming the request body are mutually exclusive in
    mitmproxy: once the `requestheaders` hook returns, a flow with `stream` set
    goes to `start_request_stream`, which raises `NotImplementedError("Can't set
    a response and enable streaming at the same time.")` — and that kills the
    whole connection instead of delivering the refusal. Only a request that
    CARRIES A BODY reaches that branch, which is what made this so hard to see:
    every refusal of a GET was delivered normally while every refused
    `POST /v1/messages` crashed the proxy, so the caller waited out its timeout
    and reported a hung platform rather than the reason it was refused. Measured
    on the dev box: 68 refused message turns over 48h, zero 503s delivered, 350
    crashes.

    Clearing the flag here rather than at each refusal site is deliberate. There
    are eight of them across `requestheaders` and more will be added; a rule that
    lives at the one point all of them go through cannot be forgotten by the
    ninth. (`http_connect` answers 407 without coming through here, and does not
    need to: a CONNECT has no body, so it never reaches the streaming branch.)

    The cost is that a refused request's body is buffered instead of streamed
    (mitmproxy offers no third option — a response is delivered only from the
    buffering path). That is not the #654 leak coming back: nothing is forwarded
    and nothing is held for the length of a turn, the body is consumed and
    dropped as soon as the refusal goes out.
    """
    flow.request.stream = False
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
    pinned = _SCOPED_BY_CLIENT.get(getattr(data.context.client, "id", ""))
    rc_host = bool(
        pinned and pinned[1].get("rc") and data.client_hello.sni in RC_EXTRA_HOSTS
    )
    if (data.client_hello.sni or "") not in ANTHROPIC_HOSTS and not rc_host:
        data.ignore_connection = True


def _scoped_claims(flow: http.HTTPFlow) -> tuple[str, dict | None]:
    """The caller's scoped token and its verified claims, or ``("", None)``.

    Pinned at CONNECT time where there is one (the proxy password carries the
    token on that listener); otherwise read off this request's own bearer.
    """
    pinned = _SCOPED_BY_CLIENT.get(getattr(flow.client_conn, "id", ""))
    if pinned:
        return pinned
    token = _caller_bearer(flow)
    claims = verify_scoped_token(token, SCOPED_SECRET) if SCOPED_SECRET else None
    return token, claims


def _answer_here(flow: http.HTTPFlow, claims: dict | None) -> bool:
    """Answer a non-model endpoint from the table, or leave it to go upstream.

    Unconditional, and before ANY upstream credential is attached — not behind
    the RC check, not behind admission. Which pool the project runs on used to
    decide who may answer these, and a session admission had positively placed
    on the subscription was let through to Anthropic — a real account asking
    about itself. That is the second half of 结论 46, and it is gone: a machine
    has ONE launch shape now, and that shape has to boot on a deployment that
    owns no subscription at all. Asking anything first would put the boot behind
    a call that can fail open, on a path whose wrong answer hands a sandbox the
    platform account's uuid and email.

    The VERIFIED place is what the answer asserts — `_attribution` lets the
    per-request header pick the topic inside an already-proven project, which is
    right for billing and wrong for identity. A caller that proved no place gets
    the same empty-identity answer rather than Anthropic's.
    """
    claims = claims or {}
    answer = control_answer(
        flow.request.host,
        flow.request.path,
        str(claims.get("p") or ""),
        str(claims.get("t") or ""),
        # The RC bridge flags describe a transport this session has only if its
        # token says so. Answering them on is what makes Claude Code open
        # `/v1/code/…`, and for a session with no RC claim `_rc_route` has
        # nowhere to send those — they would go upstream on the platform's
        # credential, carrying control-session identifiers. Off is the honest
        # answer, and it is the same one an unflagged session gets today.
        rc=bool(claims.get("rc") and claims.get("p") and claims.get("t")),
    )
    if answer is None:
        return False
    flow.server_conn.via = None
    flow.request.stream = False
    flow.response = http.Response.make(
        answer.status, answer.body, {"Content-Type": "application/json"}
    )
    return True


def _rc_route(flow: http.HTTPFlow, token: str, claims: dict | None) -> bool:
    """Route RC before any upstream credential is attached.

    RC ownership comes from the verified token's place, never the attribution
    header (which may select another topic for metering).

    The non-model startup endpoints are NOT here: they are answered by
    `_answer_here`, which runs whether or not this session has RC.
    """
    rc = bool(claims and claims.get("rc") and claims.get("p") and claims.get("t"))
    path = flow.request.path.split("?", 1)[0]
    if not rc:
        return False
    if flow.request.host in RC_EXTRA_HOSTS and not path.startswith("/v1/code/"):
        _refuse(
            flow,
            403,
            "permission_error",
            "This endpoint is outside the Cheese RC transport",
        )
        return True
    if not path.startswith("/v1/code/"):
        return False
    if not RC_BASE:
        _refuse(flow, 503, "api_error", "Cheese RC backend is not configured")
        return True
    parsed = urlparse(RC_BASE)
    flow.server_conn.via = None
    flow.request.scheme = parsed.scheme
    flow.request.host = parsed.hostname
    flow.request.port = parsed.port or (443 if parsed.scheme == "https" else 80)
    flow.request.path = parsed.path.rstrip("/") + flow.request.path
    # Strip provider credentials even when the caller carries a ccproxy ticket.
    for name in (
        "authorization",
        "x-api-key",
        "cookie",
        "proxy-authorization",
        X_ATTR_HEADER,
    ):
        flow.request.headers.pop(name, None)
    flow.request.headers["host"] = parsed.netloc
    flow.request.headers["x-cheese-token"] = token
    flow.metadata["cheese_rc"] = True
    return True


def _refuse_verdict(flow: http.HTTPFlow, verdict) -> None:
    """Send the backend's refusal back as the KIND of refusal it is.

    Both exits used to read "cheese project budget: …" with a 429, from the days
    when a spent budget was the only way a turn was ever refused. It is not any
    more: a card bound to a model this project's catalogue cannot serve is
    refused at admission too (I27 — answer or refuse, never swap the pool), and
    dressing that as an exhausted quota sends the user to top up an account that
    is fine while the card stays broken. A 429 also tells the client to back
    off and try again, which for a binding is a retry that can only fail.
    """
    if verdict.reason_kind == BINDING:
        _refuse(flow, 400, "invalid_request_error", verdict.reason)
        return
    _refuse(flow, 429, "rate_limit_error", f"cheese project budget: {verdict.reason}")


async def requestheaders(flow: http.HTTPFlow) -> None:
    # Runs at HEADER time, before the body arrives — and everything below reads
    # only headers/metadata, never the request body — so the request body can be
    # streamed straight through (flow.request.stream, set once the host is
    # allowed) instead of being buffered whole in RAM. A long agent turn re-POSTs
    # its entire grown conversation as the request body every turn; buffering
    # that (together with the response) is what OOM-kills this proxy on long
    # runs, after which the client just sees a refused connection until it
    # restarts. Refusals still work, but they must go through `_refuse` — a
    # refusal and a streamed body cannot both stand, and that is where the flag
    # is taken back.

    # Multi-host by SNI: the sandbox --add-hosts api.anthropic.com AND the login
    # hosts (console.anthropic.com, platform.claude.com) to this one proxy, so
    # interactive Claude Code's login/refresh also gets the real token injected.
    # Reverse mode would pin every request to api.anthropic.com; instead forward
    # each to the host it was actually for, read from the TLS SNI.
    sni = getattr(flow.client_conn, "sni", None)
    if sni:
        flow.request.host = sni
    if (
        getattr(flow.client_conn, "tls_established", False)
        and flow.request.host in ANTHROPIC_HOSTS | RC_EXTRA_HOSTS
    ):
        flow.request.stream = True
        # Before RC, before admission, before any credential: Claude Code's
        # identity / settings / policy / feature-flag / telemetry calls are
        # answered from the table and never leave this process (结论 46).
        token, claims = _scoped_claims(flow)
        if _answer_here(flow, claims):
            return
        if _rc_route(flow, token, claims):
            return
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

    # Host is allowed and we intend to forward: stream the body rather than
    # buffer it. A path below may still refuse, and refusing TAKES THIS BACK —
    # see `_refuse`, which is where the two decisions are reconciled. They are
    # not independent: leaving the flag on while setting a response is a fatal
    # error in mitmproxy, not a harmless contradiction.
    flow.request.stream = True

    via = _via()
    if via is not None:
        flow.server_conn.via = via

    # Attribution BEFORE anything else: the caller's own Bearer is the scoped
    # token.
    project_id, topic_id, bearer, carries_own_credential = _attribution(flow)
    flow.metadata["cheese_attr"] = (project_id, topic_id)

    is_messages = "/v1/messages" in flow.request.path
    is_subagent = bool(
        flow.request.headers.get("x-claude-code-agent-id")
    ) or flow.request.headers.get("x-claude-code-request-class") in {
        "subagent",
        "workflow",
    }
    flow.metadata["cheese_subagent"] = is_subagent

    selected_model = flow.request.headers.pop("x-cheese-child-model", "")
    child_model = selected_model if is_subagent else ""
    if child_model and child_model == PARENT_MODEL.get((project_id, topic_id)):
        # CC 的 hint 头对每个分身都打上它解析出的分身模型:没指定时那个值就
        # 是父会话(被改写前的)模型 —— 是「继承」不是「指定」。快路把它当显式
        # 送准入,gateway 项目的普通分身全灭(2026-09-24 实测,昨晚事故换了个
        # 头)。认出回显,按未指定走,与推迟路的体回显同一个判据。
        child_model = ""

    # A subagent's /v1/messages defers everything from here to the `request`
    # hook: admission honours the model the parent named for this subagent,
    # and that name sits in the request body, which has not arrived at header
    # time. Only this class pays for a buffered body — the main conversation's
    # turns, the long ones whose size is the #654 OOM, keep streaming through.
    #
    # …and only up to a size: buffered means held whole in this process's RAM,
    # so an unbounded defer would hand a caller-controlled OOM knob to the
    # shared proxy (the #654 shape, wearing a new hat). Over the cap the flow
    # simply takes the old road — streamed, admitted without a requested model,
    # bound to the subagent default. The cap is a property of the discovery
    # layer, never a refusal: nothing a real turn does is lost beyond running
    # on the default instead of a named teammate, which is what every subagent
    # did before this change.
    if is_messages and is_subagent and not child_model:
        declared = flow.request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) <= DEFER_BODY_LIMIT:
            flow.request.stream = False
            flow.metadata["cheese_deferred"] = (
                project_id,
                topic_id,
                bearer,
                carries_own_credential,
            )
            return

    verdict = None
    if project_id and ADMISSION_URL:
        verdict = await _admit(
            flow,
            project_id,
            topic_id,
            bearer,
            subagent=is_subagent,
            child_model=child_model,
        )

    _route(
        flow,
        verdict,
        is_messages=is_messages,
        project_id=project_id,
        bearer=bearer,
        carries_own_credential=carries_own_credential,
        keep_haiku=not is_subagent,
    )


async def _admit(
    flow, project_id, topic_id, bearer, *, subagent, requested_model="", child_model=""
):
    """One admission call, off the event loop and timed.

    Asked for EVERY request, not only /v1/messages. The upstream connection is
    opened by whichever request comes first, and Claude Code's startup
    api/oauth/profile check beats the first turn to it — so the identity that
    connection authenticates as has to be settled by then, or the turn's own
    ticket goes out over the wrong one. Cheap: verdicts are cached per
    (project, topic) — the topic is part of the answer, not just of the
    question, because the identity it resolves belongs to that topic's machine.
    """
    # Off-loop: urllib blocks, and one slow admission call must not stall
    # every other flow through the proxy.
    admission_started = time.perf_counter()

    def check_admission():
        check_started = time.perf_counter()
        verdict = (
            ADMISSION.check(
                project_id, topic_id, bearer, subagent=True, child_model=child_model
            )
            if subagent and child_model
            else ADMISSION.check(
                project_id,
                topic_id,
                bearer,
                subagent=True,
                requested_model=requested_model,
            )
            if subagent
            else ADMISSION.check(project_id, topic_id, bearer)
        )
        return verdict, check_started, time.perf_counter()

    verdict, check_started, check_finished = await asyncio.to_thread(check_admission)
    admission_finished = time.perf_counter()
    flow.metadata["cheese_admission_ms"] = (
        admission_finished - admission_started
    ) * 1000
    flow.metadata["cheese_admission_phases_ms"] = {
        "thread_queue": (check_started - admission_started) * 1000,
        "check": (check_finished - check_started) * 1000,
        "loop_resume": (admission_finished - check_finished) * 1000,
    }
    return verdict


async def request(flow: http.HTTPFlow) -> None:
    """The deferred half of admission, for a subagent's /v1/messages.

    Runs once the request body is whole in RAM (these flows left
    `requestheaders` with streaming off, for exactly this read): take the
    model the parent named off the body, ask admission with it, then walk the
    same routing as every other request.
    """
    deferred = flow.metadata.get("cheese_deferred")
    if not deferred:
        return
    project_id, topic_id, bearer, carries_own_credential = deferred
    requested = requested_model_of(flow.request.content or b"")
    if requested and is_haiku_name(requested):
        # CLI 自己的后台请求类(会话标题、路径建议),不是主 agent 的指定:照
        # 旧路走 —— 准入按未指定绑定,改写把它盖成分身默认,与今天逐字节一致。
        requested = ""
    if requested and requested == PARENT_MODEL.get((project_id, topic_id)):
        # 体里的模型 == 主对话被改写前的那个值:这是 CC 的「继承」长相,不是
        # 主 agent 的指定 —— fork 和不带 model 定义的分身都长这样。device
        # 启动环境不钉模型(结论 46),CC 回显的是它自己的内建默认,这个名字
        # 在准入的席位配置里不存在,送上去只会吃到一个张冠李戴的拒绝
        # (2026-09-23,gateway 项目普通分身全灭 30 分钟)。按未指定处理。
        requested = ""
    verdict = None
    if project_id and ADMISSION_URL:
        verdict = await _admit(
            flow,
            project_id,
            topic_id,
            bearer,
            subagent=True,
            requested_model=requested,
        )
    _route(
        flow,
        verdict,
        is_messages=True,
        project_id=project_id,
        bearer=bearer,
        carries_own_credential=carries_own_credential,
        keep_haiku=False,
        buffered=True,
    )


def _route(
    flow,
    verdict,
    *,
    is_messages,
    project_id,
    bearer,
    carries_own_credential,
    keep_haiku,
    buffered=False,
):
    """Refuse or forward after admission: the tail every request walks."""
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
                _refuse_verdict(flow, verdict)
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
                if buffered:
                    _write_bound_model_buffered(flow, verdict.model)
                else:
                    _write_bound_model(flow, verdict.model)
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
        if verdict is not None:
            # The subscription pool serves the haiku family itself, so the
            # CLI's own background requests stay on it.
            if buffered:
                _write_bound_model_buffered(flow, verdict.model)
            else:
                _write_bound_model(flow, verdict.model, keep_haiku=keep_haiku)

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
        if verdict is not None and not verdict.allow:
            # A refusal also produces no identity (admission only resolves one
            # for a turn it is allowing), so it would otherwise be reported as
            # the misconfiguration below — pointing whoever reads it at the
            # box's env instead of at the project's balance or its card.
            _refuse_verdict(flow, verdict)
            return
        # Which of the three it was, on the box, at the moment it happened. The
        # refusal body has to name all three because the caller cannot see the
        # deployment; the operator can, and guessing between them is what turned
        # this into a day. Project id only — never the token.
        caller = _caller_bearer(flow)
        logger.warning(
            "refusing a machine turn: no identity to send it as "
            "(project=%s admission_url=%s verdict=%s upstream=%s path=%s "
            "bearer=len:%d/dot:%s/%s)",
            project_id or "<none>",
            "set" if ADMISSION_URL else "UNSET",
            "none" if verdict is None else "received",
            "absent" if verdict is None or not verdict.upstream else "present",
            flow.request.path,
            # Shape only — enough to tell a ccproxy ticket from a stale scoped
            # token from something unexpected, and never the value itself.
            len(caller),
            "." in caller,
            caller[:12],
        )
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
    # On EVERY request that gets this far, not just messages. What gets this far
    # is everything the table did not answer, and the table answers one host —
    # so the login and refresh calls a human's `claude /login` makes against
    # console.anthropic.com and platform.claude.com come through here too, as
    # does any api.anthropic.com path Anthropic adds that no row names yet. One
    # credential for all of them; the alternative is forwarding the sandbox's
    # scoped bearer, which means nothing upstream and 401s as if the caller's
    # own auth had failed.
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


class GatewayStream(StreamingUsageExtractor):
    """Reuse the bounded SSE line reader; retain only completion/error flags."""

    def __init__(self) -> None:
        super().__init__()
        self.complete = False
        self.failed = False

    def _consume(self, raw: bytes) -> None:
        if not raw.startswith(b"data:"):
            return
        try:
            event = json.loads(raw[5:])
        except json.JSONDecodeError:
            return
        if isinstance(event, dict):
            self.complete |= event.get("type") == "message_stop"
            self.failed |= event.get("type") == "error" or isinstance(
                event.get("error"), dict
            )


def responseheaders(flow: http.HTTPFlow) -> None:
    """Stream the response body through instead of buffering it whole. A turn's
    SSE response is otherwise held in RAM for the ENTIRE turn while it buffers,
    which — together with the buffered request — is what OOM-kills this proxy on
    long runs (the client then sees a refused connection until it restarts).

    Metering is preserved: for a streamed message turn the usage is scraped from
    the SSE incrementally by a StreamingUsageExtractor as chunks pass through, so
    no full body is ever materialised. A non-streaming JSON message (small, and
    not held for the turn's duration) is left buffered so response() can meter it
    the simple way. Everything else just streams straight through."""
    resp = flow.response
    if resp is None:
        return
    if flow.metadata.get("cheese_model_missed"):
        # Left buffered on purpose: response() replaces it wholesale with the
        # refusal, and a streamed body would already be on its way to the client
        # by then. It is a short upstream error — the body it answers was never
        # sent — so nothing is held for the length of a turn.
        return
    if flow.metadata.get("cheese_pool") == GATEWAY:
        if "/v1/messages" in flow.request.path and "event-stream" in resp.headers.get(
            "content-type", ""
        ):
            extractor = GatewayStream()
            flow.metadata["cheese_gateway_stream"] = extractor

            def observe(chunk: bytes) -> bytes:
                if chunk:
                    extractor.feed(chunk)
                else:
                    extractor.close()
                return chunk

            resp.stream = observe
        else:
            resp.stream = True
        return
    is_message_200 = "/v1/messages" in flow.request.path and resp.status_code == 200
    if is_message_200 and "event-stream" in resp.headers.get("content-type", ""):
        project_id, topic_id = flow.metadata.get("cheese_attr") or ("", "")
        extractor = StreamingUsageExtractor()

        def tee(chunk: bytes) -> bytes:
            if chunk:
                extractor.feed(chunk)
            else:  # end-of-stream sentinel
                extractor.close()
                if extractor.usage:
                    METER.record(project_id, topic_id, extractor.usage, extractor.model)
            return chunk

        resp.stream = tee
    elif is_message_200:
        # Non-streaming JSON message: leave buffered for response() to meter.
        return
    else:
        resp.stream = True


def _log_gateway_timing(flow: http.HTTPFlow) -> None:
    project, topic = flow.metadata.get("cheese_attr") or ("", "")
    resp = flow.response
    stream = flow.metadata.get("cheese_gateway_stream")
    logger.info(
        "gateway_request_timing %s",
        json.dumps(
            {
                "project": project,
                "topic": topic,
                "gateway_request_id": resp.headers.get("x-litellm-call-id")
                if resp
                else None,
                "request_start": getattr(flow.request, "timestamp_start", None),
                "request_end": getattr(flow.request, "timestamp_end", None),
                "client_connection": {
                    "id": flow.client_conn.id,
                    "start": getattr(flow.client_conn, "timestamp_start", None),
                    "tls_setup": getattr(flow.client_conn, "timestamp_tls_setup", None),
                },
                "server_connection": {
                    "id": getattr(flow.server_conn, "id", None),
                    "start": getattr(flow.server_conn, "timestamp_start", None),
                    "tcp_setup": getattr(flow.server_conn, "timestamp_tcp_setup", None),
                    "tls_setup": getattr(flow.server_conn, "timestamp_tls_setup", None),
                },
                "route_ready": flow.metadata.get("cheese_route_ready"),
                "admission_ms": flow.metadata.get("cheese_admission_ms"),
                "admission_phases_ms": flow.metadata.get("cheese_admission_phases_ms"),
                "response_start": getattr(resp, "timestamp_start", None),
                "response_end": getattr(resp, "timestamp_end", None),
                "status": resp.status_code if resp else None,
                "failed": bool(getattr(flow, "error", None))
                or bool(stream and (stream.failed or not stream.complete)),
                "stream_complete": stream.complete if stream else None,
                "error_type": type(flow.error).__name__
                if getattr(flow, "error", None)
                else None,
            }
        ),
    )


_failure_reports: set[asyncio.Task] = set()


def _report_gateway_failure(flow: http.HTTPFlow) -> None:
    """Report model failures through the existing authenticated error intake.

    A clean HTTP 200 can contain an SSE error or end before message_stop.
    Client disconnects use error(), not response(), and are not model failures.
    Reporting must not hold up the stream or retain request/response contents.
    """
    resp = flow.response
    if resp is None or "/v1/messages" not in flow.request.path:
        return
    stream = flow.metadata.get("cheese_gateway_stream")
    if resp.status_code >= 500 or resp.status_code in (401, 403):
        kind, message = (
            "GatewayHTTPError",
            f"Model gateway returned HTTP {resp.status_code}",
        )
    elif stream and (stream.failed or not stream.complete):
        kind, message = (
            "GatewayStreamError",
            "Model response stream failed before completion",
        )
    else:
        return
    project, topic = flow.metadata.get("cheese_attr") or ("", "")
    request_id = resp.headers.get("x-litellm-call-id")
    logger.error("%s: %s (request_id=%s)", kind, message, request_id)
    if not ADMISSION_URL or not SCOPED_SECRET or not project or not topic:
        return
    if len(_failure_reports) >= 8:
        logger.warning("model failure report capacity exceeded")
        return
    body = json.dumps(
        {
            "project_id": project,
            "topic_id": topic,
            "errors": [
                {
                    "message": message,
                    "exc_type": kind,
                    "where": "model gateway",
                    "request_id": request_id,
                }
            ],
        }
    ).encode()
    endpoint = ADMISSION_URL.removesuffix("/llm/admission") + "/backend-errors"

    def post() -> None:
        req = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Cheese-Token": SCOPED_SECRET,
            },
        )
        with urlopen(req, timeout=3) as response:
            response.read(1024)

    async def report() -> None:
        try:
            await asyncio.to_thread(post)
        except Exception:
            # Do not print an HTTP error body: it may echo credentials.
            logger.warning("model failure report could not reach backend")

    task = asyncio.create_task(report())
    _failure_reports.add(task)
    task.add_done_callback(_failure_reports.discard)


def _answer_a_missed_binding(flow: http.HTTPFlow) -> bool:
    """Turn a dropped body into a refusal that names why (I27).

    The binding could not be written into this request, so `_write_bound_model`
    forwarded none of the body and the pool answered the empty request with an
    error of its own — an error about JSON, which says nothing about the card.
    Replacing it here is the only way the reason reaches the client: mitmproxy
    decides streaming when `requestheaders` returns, and a flow already
    streaming its request body cannot be given a response (see `_refuse`), so
    the first moment a refusal can be DELIVERED is when the pool's own answer
    comes back. Nothing was spent to get it: the pool was sent an empty body.
    """
    model = flow.metadata.get("cheese_model_missed")
    if not model or flow.response is None:
        return False
    logger.error(
        "refusing a turn whose body never named a model in its head: "
        "binding=%s pool=%s upstream_status=%s",
        model,
        flow.metadata.get("cheese_pool") or "subscription",
        flow.response.status_code,
    )
    flow.response = http.Response.make(
        400,
        json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": (
                        "cheese: this request's body did not name a model in "
                        f"its first {MODEL_REWRITE_LIMIT} bytes, so the model "
                        f"bound on the card ({model}) could not be written into "
                        "it; refusing rather than running the turn on another "
                        "model"
                    ),
                },
            }
        ).encode(),
        {"Content-Type": "application/json"},
    )
    return True


def error(flow: http.HTTPFlow) -> None:
    if flow.metadata.get("cheese_pool") == GATEWAY:
        _log_gateway_timing(flow)


def response(flow: http.HTTPFlow) -> None:
    if _answer_a_missed_binding(flow):
        return
    if flow.metadata.get("cheese_pool") == GATEWAY:
        _log_gateway_timing(flow)
        _report_gateway_failure(flow)
        return
    # SSE turns are metered incrementally in the responseheaders streaming tee;
    # the only body still buffered here is a non-streaming JSON message.
    if "/v1/messages" not in flow.request.path or not flow.response:
        return
    if flow.response.status_code != 200:
        return
    if "event-stream" in flow.response.headers.get("content-type", ""):
        return
    project_id, topic_id = flow.metadata.get("cheese_attr") or ("", "")
    try:
        payload = json.loads(flow.response.raw_content or b"")
    except (json.JSONDecodeError, ValueError):
        return
    usage, model = payload.get("usage", {}), payload.get("model", "")
    if usage:
        METER.record(project_id, topic_id, usage, model)
