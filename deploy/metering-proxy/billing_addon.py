"""mitmproxy addon: meter every Claude turn, cap it, and route it.

This sits between a caller's Claude Code and the upstream. The caller is a
session on the platform's central session host, and its Authorization is a
placeholder that authenticates nothing. This proxy holds the platform's
Claude credentials and selects an account for each conversation bound for
Anthropic, retaining that account until it is unavailable.

That placement makes this the only point that can:
  - meter a subscription turn's real cost (the subscription path deliberately
    skips LiteLLM — no per-call key to meter, and re-originating from our own
    client would change what the provider sees),
  - enforce a cap BEFORE forwarding, so an exhausted budget cannot overspend:
    per-project via the backend's /llm/admission (#218), plus the rolling
    token window as the deployment-wide backstop,
  - send a project that admission places on the API-key pool to LiteLLM
    instead, with the project's virtual key; the platform's credential must
    never reach the gateway.

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

A third, `--mode reverse:https://chatgpt.com@8445`, is not for sessions: it is
where the API-key pool (LiteLLM) sends a ChatGPT subscription request, on
`/chatgpt/<account>/…`, to have one of the platform's ChatGPT accounts put on
it. See `_forward_to_chatgpt`.

Config (env): CHEESE_USAGE_LOG, CHEESE_TOKEN_CAP, CHEESE_CAP_WINDOW_S,
CHEESE_SCOPED_SECRET, CHEESE_ALLOW_HEADER_ATTR, CHEESE_ADMISSION_URL,
CHEESE_ADMISSION_CACHE_S, CHEESE_GATEWAY_BASE, CHEESE_CHATGPT_CREDENTIALS,
CHEESE_CHATGPT_KEY. The Codex client version sent to ChatGPT is not env: it
is a file beside the ChatGPT accounts (ChatGPTAccounts.client_version).
"""

import asyncio
import hmac
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from mitmproxy import http, tls

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_accounts import ClaudeAccounts  # noqa: E402
from claude_retry import ATTEMPTS, Attempt, install as install_retry  # noqa: E402
from cheese_billing_core import (  # noqa: E402
    ANTHROPIC_HOSTS,
    BINDING,
    CHATGPT_UPSTREAM,
    EXPIRED,
    GATEWAY,
    MODEL_REWRITE_LIMIT,
    NO_CREDENTIAL,
    NO_LOGIN_PLACEHOLDER,
    AdmissionGate,
    ChatGPTAccounts,
    CodexBody,
    Egress,
    Meter,
    ModelRewrite,
    PlatformCredential,
    StreamingUsageExtractor,
    chatgpt_route,
    control_answer,
    is_haiku_name,
    no_login_answer,
    proxy_basic_password,
    requested_model_of,
    verify_scoped_token,
    with_client_version,
)

logger = logging.getLogger("cheese.metering")

X_ATTR_HEADER = "x-cheese-attr"  # legacy "<project_id>/<topic_id>", spoofable
USAGE_LOG = Path(os.environ.get("CHEESE_USAGE_LOG", "/var/log/cheese/usage.jsonl"))
# 0 disables the backstop cap. Set per deployment from the subscription's ceiling.
TOKEN_CAP = int(os.environ.get("CHEESE_TOKEN_CAP", "0"))
CAP_WINDOW_S = int(os.environ.get("CHEESE_CAP_WINDOW_S", str(5 * 3600)))
# Shared with the backend (SANDBOX_TOKEN): verifies the caller's scoped token,
# and is the proxy's own credential towards the backend.
SCOPED_SECRET = os.environ.get("CHEESE_SCOPED_SECRET", "")
ALLOW_HEADER_ATTR = os.environ.get("CHEESE_ALLOW_HEADER_ATTR", "") == "1"
ADMISSION_URL = os.environ.get("CHEESE_ADMISSION_URL", "")
# How large a deferred subagent body may be and still be buffered whole for the
# requested-model read (see the defer branch in `requestheaders`). Real subagent
# turns sit orders of magnitude below this; past it the flow keeps the old
# streamed behaviour, which is a floor, not a failure.
DEFER_BODY_LIMIT = int(os.environ.get("CHEESE_DEFER_BODY_LIMIT", str(8 * 1024 * 1024)))
ADMISSION_CACHE_S = float(os.environ.get("CHEESE_ADMISSION_CACHE_S", "30"))

# Where the API-key pool lives (LiteLLM). A project whose supply decision says
# `gateway` is rewritten to this base instead of going out to Anthropic —
# same interception point, different destination (#243). Empty = this
# deployment has no API-key pool, and such a project is refused rather than
# silently served from the subscription it did not ask for.
GATEWAY_BASE = os.environ.get("CHEESE_GATEWAY_BASE", "")  # "http://host:port"

METER = Meter(USAGE_LOG, CAP_WINDOW_S)
# The platform's Claude credential: the one thing that authenticates a
# subscription request. Sessions never hold it. See PlatformCredential.
CREDENTIAL = PlatformCredential(
    Path(
        os.environ.get(
            "CHEESE_CLAUDE_CREDENTIAL", "/etc/cheese/claude-credential/credential"
        )
    )
)
CLAUDE_ACCOUNTS = ClaudeAccounts(CREDENTIAL)


def load(loader):
    install_retry()


# The proxy's own credential rides every admission call: only a caller holding
# it is handed a gateway project's key (the session's bearer names the room,
# and a session must never hold that key).
ADMISSION = AdmissionGate(
    ADMISSION_URL, cache_s=ADMISSION_CACHE_S, credential=SCOPED_SECRET
)

# The platform's ChatGPT accounts, one directory per name. See ChatGPTAccounts.
CHATGPT_ACCOUNTS = ChatGPTAccounts(
    Path(os.environ.get("CHEESE_CHATGPT_CREDENTIALS", "/etc/cheese/chatgpt-credential"))
)
# What a caller of the ChatGPT listener must present as its Bearer: the api_key
# of the gateway's deployments that point here. The listener is on a private
# network shared only with the gateway; the key is the second line of defence,
# since a request that gets through spends a subscription. Unset refuses every
# request.
CHATGPT_KEY = os.environ.get("CHEESE_CHATGPT_KEY", "")

if not ADMISSION_URL:
    # Said once, loudly, at load: an unset env var produces no error anywhere
    # downstream, and what it switches off is invisible from outside — the
    # budget never refuses, and no project is ever routed to the API-key pool.
    # A deployment that means it can read this line and move on.
    logger.warning(
        "CHEESE_ADMISSION_URL is unset: per-project budgets are NOT enforced "
        "(only the rolling token cap), and every request goes to the "
        "subscription."
    )

# What each client connection PROVED at CONNECT time: (scoped token, claims).
# The request's Bearer is the caller's Claude credential, so attribution cannot
# come from it. The scoped token is not missing though: the tunnel helper
# stamps it as the CONNECT's proxy password, which http_connect already
# verifies to open the tunnel at all. Keeping the verified result turns that
# check into the source of attribution instead of a fact thrown away.
_SCOPED_BY_CLIENT: dict[str, tuple[str, dict]] = {}


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


def _attribution(flow: http.HTTPFlow) -> tuple[str, str, str]:
    """(project_id, topic_id, scoped_token) for this request.

    Verified claims first; the spoofable header only where explicitly allowed.
    Empty project = unattributable (recorded as such; refused separately when
    scoped auth is required).

    Two places carry verified claims. A session's Bearer is its Claude
    credential, so its scoped token is the one it proved when it opened the
    tunnel; a Bearer that is itself a scoped token is honoured too.

    The third element is what authenticates the admission call, so it must be
    the scoped token and not merely whatever was in the Authorization header —
    handing admission a Claude credential 401s, and the proxy fails OPEN on
    admission errors, which would leave the budget brake silently not braking.
    """
    bearer = _caller_bearer(flow)
    if SCOPED_SECRET:
        claims = verify_scoped_token(bearer, SCOPED_SECRET)
        if claims:
            return str(claims.get("p") or ""), str(claims.get("t") or ""), bearer
        pinned = _SCOPED_BY_CLIENT.get(getattr(flow.client_conn, "id", ""))
        if pinned:
            token, connect_claims = pinned
            project = str(connect_claims.get("p") or "")
            return project, _topic_within(flow, project, connect_claims), token
    if ALLOW_HEADER_ATTR:
        attr = flow.request.headers.get(X_ATTR_HEADER, "")
        project, _, topic = attr.partition("/")
        return project, topic, bearer
    return "", "", bearer


def _route_to_gateway(flow: http.HTTPFlow, key: str) -> bool:
    """Send this request to the API-key pool (LiteLLM) instead of Anthropic.

    Returns False when the deployment has no gateway configured or the control
    plane could not supply the project's virtual key — the caller refuses in
    that case. Serving such a request from the subscription instead would bill
    a pool the project did not choose, which is the exact confusion this whole
    routing decision exists to remove.
    """
    if not GATEWAY_BASE or not key:
        return False
    parsed = urlparse(GATEWAY_BASE)
    if not parsed.hostname:
        return False
    flow.request.scheme = parsed.scheme or "http"
    flow.request.host = parsed.hostname
    flow.request.port = parsed.port or (443 if flow.request.scheme == "https" else 80)
    flow.request.headers["host"] = parsed.netloc
    # mitmproxy hands this flow the server connection the previous request on
    # the same client connection opened, egress included when that request
    # carried the platform credential. The gateway is local: never through it.
    flow.server_conn.via = None
    # LiteLLM authenticates with the project's virtual key. The caller's own
    # Claude credential is in this header and must NOT ride along to the
    # gateway, where it would sit in another service's reach and logs;
    # x-api-key would also override the bearer downstream.
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


def _refuse(
    flow: http.HTTPFlow,
    status: int,
    kind: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> None:
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
        {"Content-Type": "application/json", **(headers or {})},
    )


def _refuse_reached_cap(
    flow: http.HTTPFlow, message: str, reopens_at: int | None
) -> None:
    """Refuse a turn that a spent allowance stops — the project's budget, or
    the usage limit of the subscription account behind its model — in the
    shape Claude Code's gateway contract gives a reached spend cap: 429
    ``billing_error`` with ``x-should-retry: false`` and the
    ``anthropic-ratelimit-unified-*`` rejection headers.

    A bare 429 is what it used to be, and Claude Code reads that as throttling:
    it retries the turn up to ten times and, for a session signed in the way
    ours are, prints "Server is temporarily limiting requests (not your usage
    limit)" — the opposite of what happened. With the disabled-reason header it
    prints ``message`` as it stands and does not retry. ``representative-claim``
    and ``overage-status`` stay off on purpose: with them the client composes
    its own limit line and drops ours.
    """
    headers = {
        "x-should-retry": "false",
        "anthropic-ratelimit-unified-status": "rejected",
        "anthropic-ratelimit-unified-overage-utilization": "1",
        "anthropic-ratelimit-unified-overage-surpassed-threshold": "1",
        "anthropic-ratelimit-unified-overage-disabled-reason": "org_spend_cap_reached",
    }
    if reopens_at is not None:
        headers["retry-after"] = str(max(0, reopens_at - int(time.time())))
        headers["anthropic-ratelimit-unified-reset"] = str(reopens_at)
        headers["anthropic-ratelimit-unified-overage-reset"] = str(reopens_at)
    _refuse(flow, 429, "billing_error", message, headers)


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
        # Kept, not discarded: this is the only scoped token on the whole
        # connection — the request Bearer is the session's Claude credential.
        # See _attribution.
        _SCOPED_BY_CLIENT[getattr(flow.client_conn, "id", "")] = (password, claims)
        return
    flow.response = http.Response.make(
        407,
        b"cheese: a valid scoped token is required as the proxy password",
        {"Proxy-Authenticate": 'Basic realm="cheese-metering"'},
    )


def _leave_through_egress(flow: http.HTTPFlow, credential=None) -> bool:
    """Send a request carrying the platform's credential out through the
    credential's egress, when it has one. Only these requests: the gateway,
    the answers given here and everything tunnelled raw keep their own route.
    No egress reachable means no request: the upstream connection fails and
    the client is told so, never quietly sent direct instead. False when the
    request has been refused here instead."""
    egress = (credential or CREDENTIAL).egress()
    if egress is None:
        flow.server_conn.via = None
        flow.metadata.pop("cheese_egress", None)
        return True
    if egress == _REFUSED_EGRESS.get("egress"):
        # Refused the proxy's credentials once, it will again until someone
        # changes them; a 502 would be retried silently for minutes.
        _refuse(
            flow,
            400,
            "invalid_request_error",
            f"cheese: the Claude credential's egress {egress.host}:"
            f"{egress.port} refused the proxy's credentials; fix them with "
            "`claude-login.sh egress set`",
        )
        return False
    if EGRESS_HEALTH.unreachable(egress):
        _refuse(
            flow,
            503,
            "api_error",
            EGRESS_OFFLINE_MESSAGE,
            {"x-should-retry": "false"},
        )
        return False
    flow.server_conn.via = ("http", (egress.host, egress.port))
    flow.metadata["cheese_egress"] = egress
    # The upstream CONNECT is a flow of its own, raised when the lazy server
    # connection opens; the client connection is what the two share.
    _EGRESS_AUTH_BY_CLIENT[getattr(flow.client_conn, "id", "")] = egress.authorization
    return True


# The egress that last answered a CONNECT with 407, until its line changes.
_REFUSED_EGRESS: dict[str, Egress] = {}

# The Proxy-Authorization each client connection's egress wants, set when a
# request is sent through it and read when the connection to it is opened.
_EGRESS_AUTH_BY_CLIENT: dict[str, str] = {}

# What a turn is told when the machine its credential must leave through does
# not answer. 503 with x-should-retry: false is the one answer Claude Code
# gives up on at once; a 502 from a connection that never opened was retried
# ten times, each attempt waiting out a TCP connect timeout, and the room sat
# silent for minutes. The backend recognises the phrase "subscription egress
# is offline" and shows its own notice for it (`platform_failures`), which
# takes only short lines: Claude Code prints this inside the JSON error body.
EGRESS_OFFLINE_MESSAGE = (
    "cheese: the subscription egress is offline, so no model call was made. "
    "It works again once that machine is back online."
)
#: One attempt to open a connection to an egress may take this long. Over the
#: tailnet a healthy one answers in well under a second.
EGRESS_CONNECT_TIMEOUT_S = 4.0
#: Attempts before an egress is called offline, and the pause between them: a
#: blip of a few seconds must not fail a turn.
EGRESS_ATTEMPTS = 3
EGRESS_ATTEMPT_GAP_S = 1.0
#: How long a verdict stands before a request asks again. An offline verdict
#: is short so that a machine coming back is used within seconds.
EGRESS_UP_FOR_S = 15.0
EGRESS_DOWN_FOR_S = 5.0


class EgressHealth:
    """Whether each egress answers, asked before a request is sent through it.

    One probe per egress at a time: the requests that arrive while it runs wait
    for its answer instead of opening probes of their own."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._verdicts: dict[tuple[str, int], tuple[bool, float]] = {}
        self._probes: dict[tuple[str, int], asyncio.Task] = {}

    async def check(self, egress: Egress) -> None:
        address = (egress.host, egress.port)
        verdict = self._verdicts.get(address)
        if verdict is not None and self._clock() < verdict[1]:
            return
        probe = self._probes.get(address)
        if probe is None:
            probe = asyncio.ensure_future(self._probe(address))
            self._probes[address] = probe
        try:
            await asyncio.shield(probe)
        finally:
            if probe.done() and self._probes.get(address) is probe:
                del self._probes[address]

    async def _probe(self, address: tuple[str, int]) -> None:
        for attempt in range(EGRESS_ATTEMPTS):
            if attempt:
                await asyncio.sleep(EGRESS_ATTEMPT_GAP_S)
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(*address), EGRESS_CONNECT_TIMEOUT_S
                )
            except (OSError, TimeoutError):
                continue
            writer.close()
            self._verdicts[address] = (True, self._clock() + EGRESS_UP_FOR_S)
            return
        logger.warning(
            "the Claude credential's egress %s:%s did not answer %d attempts",
            *address,
            EGRESS_ATTEMPTS,
        )
        self._verdicts[address] = (False, self._clock() + EGRESS_DOWN_FOR_S)

    def unreachable(self, egress: Egress) -> bool:
        """The last answer for this egress was that it is offline."""
        verdict = self._verdicts.get((egress.host, egress.port))
        return verdict is not None and not verdict[0]

    def forget(self, egress: Egress) -> None:
        """A connection through it failed: ask again before the next request."""
        self._verdicts.pop((egress.host, egress.port), None)


EGRESS_HEALTH = EgressHealth()


async def _check_egresses(flow: http.HTTPFlow, verdict, is_messages: bool) -> None:
    """Ask, before routing, whether the egresses of the platform's Claude
    credentials answer — only for a request that will be sent on one of them.
    A project the gateway serves never waits on an egress it does not use."""
    if _caller_bearer(flow) != NO_LOGIN_PLACEHOLDER:
        return
    if (
        is_messages
        and verdict is not None
        and verdict.allow
        and verdict.pool == GATEWAY
    ):
        return
    egresses = {
        (egress.host, egress.port): egress
        for credential in CLAUDE_ACCOUNTS.accounts().values()
        if (egress := credential.egress()) is not None
    }
    await asyncio.gather(*(EGRESS_HEALTH.check(e) for e in egresses.values()))


def http_connect_upstream(flow: http.HTTPFlow) -> None:
    """Authenticate the CONNECT to a credential's egress."""
    if flow.request.host == CHATGPT_UPSTREAM[0]:
        # One client connection (the gateway's) carries many accounts, so a
        # ChatGPT account's egress login is found by the egress this CONNECT
        # is sent to (the flow's server); ChatGPTAccounts.egress_conflict keeps
        # that unambiguous.
        address = getattr(flow.server_conn, "address", None)
        authorization = _CHATGPT_EGRESS_AUTH.get(tuple(address)) if address else ""
        if authorization:
            flow.request.headers["Proxy-Authorization"] = authorization
        return
    authorization = _EGRESS_AUTH_BY_CLIENT.get(getattr(flow.client_conn, "id", ""))
    if authorization:
        flow.request.headers["Proxy-Authorization"] = authorization


# The Proxy-Authorization each ChatGPT account egress wants, by its address.
_CHATGPT_EGRESS_AUTH: dict[tuple[str, int], str] = {}

# What the gateway sends that must not reach ChatGPT: its own key for this
# listener, anything that would stand in for the account's credential or
# identity, and proxy headers meant for this hop.
_CHATGPT_STRIPPED = (
    "authorization",
    "chatgpt-account-id",
    "cookie",
    "openai-organization",
    "openai-project",
    "originator",
    "proxy-authorization",
    "proxy-connection",
    "version",
    "x-api-key",
)


def _on_chatgpt_listener(flow: http.HTTPFlow) -> bool:
    mode = getattr(flow.client_conn, "proxy_mode", None)
    return getattr(mode, "type_name", "") == "reverse" and tuple(
        getattr(mode, "address", ()) or ()
    ) == tuple(CHATGPT_UPSTREAM)


async def _forward_to_chatgpt(flow: http.HTTPFlow) -> None:
    """Put one of the platform's ChatGPT accounts on a gateway request.

    `/chatgpt/<account>/responses…` and `/chatgpt/<account>/models` go to
    `https://chatgpt.com/backend-api/codex/…` on that account's token and
    egress; every other path, an unknown account, and an account with no
    usable login are answered here and never forwarded. The request is
    otherwise passed as it came, body streamed.

    Plain HTTP and TLS are both accepted: the hop is the box's own docker
    bridge, and mitmproxy's reverse mode takes either on one port.

    Not metered: the gateway records its own spend for these, and the ledger's
    usage reader understands Anthropic's SSE, not the Responses API's.
    """
    flow.metadata["cheese_chatgpt"] = True
    presented = _caller_bearer(flow)
    if not CHATGPT_KEY or not hmac.compare_digest(
        presented.encode(), CHATGPT_KEY.encode()
    ):
        _refuse(
            flow,
            401,
            "authentication_error",
            "cheese: this listener needs CHEESE_CHATGPT_KEY as the Bearer"
            if CHATGPT_KEY
            else "cheese: CHEESE_CHATGPT_KEY is not configured on this proxy",
        )
        return
    route = chatgpt_route(flow.request.path)
    if route is None or not route[1]:
        _refuse(
            flow,
            404,
            "not_found_error",
            "cheese: only /chatgpt/<account>/responses and "
            "/chatgpt/<account>/models are forwarded",
        )
        return
    name, upstream_path = route
    account = CHATGPT_ACCOUNTS.get(name)
    if account is None:
        _refuse(
            flow,
            404,
            "not_found_error",
            f"cheese: no ChatGPT account named {name!r} on this proxy",
        )
        return
    if account.refresh_due():
        await asyncio.to_thread(account.refresh_if_due)
    token, account_id, missing = account.token()
    if not token:
        if missing == EXPIRED:
            _refuse(
                flow,
                503,
                "api_error",
                f"cheese: ChatGPT account {name!r} has expired and is being "
                "renewed; retry shortly",
            )
        else:
            _refuse(
                flow,
                401,
                "authentication_error",
                f"cheese: ChatGPT account {name!r} has no usable login; an "
                f"operator has to run chatgpt-login.sh login {name}",
            )
        return
    egress = account.egress()
    if egress is not None:
        other = CHATGPT_ACCOUNTS.egress_conflict(name, egress)
        if other:
            _refuse(
                flow,
                503,
                "api_error",
                f"cheese: ChatGPT accounts {name!r} and {other!r} use the egress "
                f"{egress.host}:{egress.port} with different logins; give "
                "them the same one or different proxies",
            )
            return
        _CHATGPT_EGRESS_AUTH[(egress.host, egress.port)] = egress.authorization
    for header in _CHATGPT_STRIPPED:
        flow.request.headers.pop(header, None)
    # Read once per request so the header and the model list's query name the
    # same version even while the file is being changed.
    version = CHATGPT_ACCOUNTS.client_version()
    flow.request.scheme = "https"
    flow.request.host, flow.request.port = CHATGPT_UPSTREAM
    flow.request.path = with_client_version(upstream_path, version)
    flow.request.headers["host"] = CHATGPT_UPSTREAM[0]
    flow.request.headers["authorization"] = f"Bearer {token}"
    if account_id:
        flow.request.headers["chatgpt-account-id"] = account_id
    flow.request.headers["originator"] = "cheese"
    flow.request.headers["version"] = version
    # Set for a direct account too: the gateway sends every account down one
    # client connection, and the flow's server connection carries whatever
    # egress the previous request on it was given.
    flow.server_conn.via = ("http", (egress.host, egress.port)) if egress else None
    flow.metadata["cheese_chatgpt_account"] = name
    if flow.request.method == "POST" and upstream_path.startswith(
        "/backend-api/codex/responses"
    ):
        # The body changes length, so it is re-framed as chunked, as
        # `_write_bound_model` does. A request that is not streamed still fails
        # upstream ("Stream must be set to true"); turning its answer back into
        # one JSON document would mean buffering it.
        body = CodexBody()
        flow.request.headers.pop("content-length", None)
        flow.request.headers["transfer-encoding"] = "chunked"
        flow.request.stream = body.feed
    else:
        flow.request.stream = True


def client_disconnected(client) -> None:
    """Release request spools and attribution when their connection closes."""
    for flow_id, attempt in list(ATTEMPTS.items()):
        if attempt.flow.client_conn.id == client.id:
            CLAUDE_ACCOUNTS.release(attempt.account, flow_id)
            attempt.close()
            ATTEMPTS.pop(flow_id)
    _SCOPED_BY_CLIENT.pop(getattr(client, "id", ""), None)
    _EGRESS_AUTH_BY_CLIENT.pop(getattr(client, "id", ""), None)


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
    admission. Which pool the project runs on used to
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
    )
    if answer is None:
        body = (
            no_login_answer(flow.request.host, flow.request.path)
            if _caller_bearer(flow) == NO_LOGIN_PLACEHOLDER
            and not CREDENTIAL.token()[0]
            else None
        )
        if body is None:
            return False
        flow.request.stream = False
        flow.response = http.Response.make(
            200, body, {"Content-Type": "application/json"}
        )
        return True
    flow.request.stream = False
    flow.response = http.Response.make(
        answer.status, answer.body, {"Content-Type": "application/json"}
    )
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
    _refuse_reached_cap(
        flow, f"cheese project budget: {verdict.reason}", verdict.reopens_at
    )


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

    if _on_chatgpt_listener(flow):
        await _forward_to_chatgpt(flow)
        return

    # Multi-host by SNI: api.anthropic.com AND the login hosts
    # (console.anthropic.com, platform.claude.com) are served here. Reverse
    # mode would pin every
    # request to api.anthropic.com; instead forward each to the host it was
    # actually for, read from the TLS SNI.
    sni = getattr(flow.client_conn, "sni", None)
    if sni:
        flow.request.host = sni
    if (
        getattr(flow.client_conn, "tls_established", False)
        and flow.request.host in ANTHROPIC_HOSTS
    ):
        flow.request.stream = True
        # Before admission, before any credential: Claude Code's identity /
        # settings / policy / feature-flag / telemetry calls are answered from
        # the table and never leave this process (结论 46).
        _, claims = _scoped_claims(flow)
        if _answer_here(flow, claims):
            return
        flow.request.headers["host"] = sni

    # Only the Anthropic names are served, and only over TLS the proxy
    # terminated. A caller naming any other host (an arbitrary SNI on the
    # reverse listener, a plain-HTTP proxy request on the CONNECT one) is
    # refused, not forwarded: this proxy relays Claude credentials and meters
    # Anthropic traffic, and is no general-purpose relay. Non-Anthropic HTTPS
    # through the CONNECT listener never reaches here (tls_clienthello tunnels
    # it raw).
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

    # Attribution BEFORE anything else.
    project_id, topic_id, bearer = _attribution(flow)
    flow.metadata["cheese_attr"] = (project_id, topic_id)

    is_messages = "/v1/messages" in flow.request.path
    is_subagent = bool(
        flow.request.headers.get("x-claude-code-agent-id")
    ) or flow.request.headers.get("x-claude-code-request-class") in {
        "subagent",
        "workflow",
    }

    selected_model = flow.request.headers.pop("x-cheese-child-model", "")
    # 小快家族的名字不是「指定」——CLI 自己的后台类请求(会话标题、路径建议、
    # WebFetch 的摘要子请求)就带着它,而 harness 的传输层把这具请求体顶层的
    # model 抄进了这个头。请求体那条路已经这么判(见 `request` 钩子与
    # `is_haiku_name`),这一条头部快路是它的早期分支,判据要一样:漏了就成了
    # 「主 agent 指名了一个目录里没有的模型」,一个目录外的小快名字把整个工具
    # 调用 400 掉。
    if is_subagent and is_haiku_name(selected_model):
        selected_model = ""
    child_model = selected_model if is_subagent else ""

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
            flow.metadata["cheese_deferred"] = (project_id, topic_id, bearer)
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

    await _refresh_credential()
    await _check_egresses(flow, verdict, is_messages)
    _route(
        flow,
        verdict,
        is_messages=is_messages,
        project_id=project_id,
        bearer=bearer,
        keep_haiku=not is_subagent,
    )


async def _admit(
    flow, project_id, topic_id, bearer, *, subagent, requested_model="", child_model=""
):
    """One admission call, off the event loop and timed.

    Cheap: verdicts are cached per (project, topic).
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
    project_id, topic_id, bearer = deferred
    requested = requested_model_of(flow.request.content or b"")
    if requested and is_haiku_name(requested):
        # CLI 自己的后台请求类(会话标题、路径建议),不是主 agent 的指定:照
        # 旧路走 —— 准入按未指定绑定,改写把它盖成分身默认,与今天逐字节一致。
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
    await _refresh_credential()
    await _check_egresses(flow, verdict, True)
    _route(
        flow,
        verdict,
        is_messages=True,
        project_id=project_id,
        bearer=bearer,
        keep_haiku=False,
        buffered=True,
    )


async def _refresh_credential() -> None:
    """Renew the platform's pair before a request needs it, off the event loop:
    the refresh is a blocking HTTP call, and one slow token endpoint must not
    stall every other flow through the proxy."""
    for credential in CLAUDE_ACCOUNTS.accounts().values():
        if credential.refresh_due():
            await asyncio.to_thread(credential.refresh_if_due)


def _retry_claude(attempt):
    flow = attempt.flow
    CLAUDE_ACCOUNTS.reject(
        attempt.account, flow.response.headers, flow.response.content or b""
    )
    if attempt.replayed or not attempt.complete or attempt.size > 64 * 1024 * 1024:
        return False
    selected = CLAUDE_ACCOUNTS.select(
        tuple(flow.metadata.get("cheese_attr", ())),
        excluded=(attempt.account,),
        request_id=flow.id,
    )
    if selected is None:
        return False
    name, credential = selected
    # A different egress cannot reuse the first account's connection.
    if not _leave_through_egress(flow, credential):
        CLAUDE_ACCOUNTS.release(name, flow.id)
        return False
    token, _ = credential.token()
    flow.request.headers["authorization"] = f"Bearer {token}"
    logger.info(
        "Claude request %s switches %s -> %s after 429", flow.id, attempt.account, name
    )
    attempt.account = name
    return True


def _refuse_without_credential(flow, verdict, missing: str) -> None:
    """Refuse a request bound for Anthropic when the platform holds no usable
    Claude credential, as a refusal the client retries only when retrying can
    help: Claude Code retries a 5xx silently for about three minutes before a
    turn says anything, and does not retry a 400."""
    if missing == EXPIRED:
        _refuse(
            flow,
            503,
            "api_error",
            "cheese: the platform's Claude login has expired and is being "
            "renewed; retry shortly",
        )
        return
    if verdict is None or verdict.fail_open:
        # Nobody answered where this goes. Without a credential only the
        # gateway could serve it, and its key comes from admission: wait.
        _refuse(
            flow,
            503,
            "api_error",
            "cheese: the control plane could not say where this request goes, "
            "and the platform has no Claude login to fall back on; retry "
            "shortly",
        )
        return
    reason = (
        "the platform has no Claude login"
        if missing == NO_CREDENTIAL
        else "the platform's Claude login was refused and has to be renewed"
    )
    _refuse(
        flow,
        400,
        "invalid_request_error",
        f"cheese: {reason}, so this project's subscription model cannot be "
        "served; an operator has to log in with "
        "deploy/metering-proxy/claude-login.sh",
    )


def _route(
    flow,
    verdict,
    *,
    is_messages,
    project_id,
    bearer,
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
            # step below (the rolling cap); a gateway project leaves here and
            # none of it applies.
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

    # Everything from here goes to Anthropic, on the platform's credential.
    selected = None
    if _caller_bearer(flow) == NO_LOGIN_PLACEHOLDER:
        if is_messages:
            selected = CLAUDE_ACCOUNTS.select(
                tuple(flow.metadata.get("cheese_attr", ())), request_id=flow.id
            )
            if selected is None and CLAUDE_ACCOUNTS.state:
                _refuse(
                    flow,
                    429,
                    "rate_limit_error",
                    "cheese: all Claude accounts are cooling down",
                )
                wait = CLAUDE_ACCOUNTS.retry_after()
                if wait is not None:
                    flow.response.headers["retry-after"] = str(wait)
                return
        credential = selected[1] if selected else CREDENTIAL
        token, missing = credential.token()
        if token:
            flow.request.headers["authorization"] = f"Bearer {token}"
            if not _leave_through_egress(flow, credential):
                if selected:
                    CLAUDE_ACCOUNTS.release(selected[0], flow.id)
                return
        else:
            _refuse_without_credential(flow, verdict, missing)
            return

    if is_messages:
        if METER.would_exceed(TOKEN_CAP):
            if selected:
                CLAUDE_ACCOUNTS.release(selected[0], flow.id)
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

    # A stale x-api-key would override the session's bearer upstream.
    flow.request.headers.pop("x-api-key", None)
    if selected:
        attempt = Attempt(flow, selected[0], _retry_claude)
        ATTEMPTS[flow.id] = attempt
        if buffered:
            attempt.tee(flow.request.raw_content or b"")
            attempt.complete = True
        else:
            rewrite = flow.request.stream

            def retain(chunk):
                out = rewrite(chunk) if callable(rewrite) else chunk
                attempt.tee(out)
                if not chunk:
                    attempt.complete = True
                return out

            flow.request.stream = retain


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
    attempt = ATTEMPTS.get(flow.id)
    if attempt:
        if resp.status_code == 429:
            resp.stream = False
            return
        if resp.status_code == 200:
            CLAUDE_ACCOUNTS.accepted(attempt.account, flow.id)
        else:
            CLAUDE_ACCOUNTS.release(attempt.account, flow.id)
    if flow.metadata.get("cheese_chatgpt"):
        resp.stream = True
        return
    if flow.metadata.get("cheese_model_missed"):
        # Left buffered on purpose: response() replaces it wholesale with the
        # refusal, and a streamed body would already be on its way to the client
        # by then. It is a short upstream error — the body it answers was never
        # sent — so nothing is held for the length of a turn.
        return
    if flow.metadata.get("cheese_pool") == GATEWAY:
        if resp.status_code == 429:
            # Left buffered: response() replaces a 429 that reports a spent
            # allowance with one Claude Code reads as a reached cap. A short
            # error body, so nothing is held for the length of a turn.
            return
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
        encoding = resp.headers.get("content-encoding", "")
        extractor = StreamingUsageExtractor(encoding)

        def tee(chunk: bytes) -> bytes:
            if chunk:
                extractor.feed(chunk)
            else:  # end-of-stream sentinel
                extractor.close()
                if extractor.usage:
                    METER.record(project_id, topic_id, extractor.usage, extractor.model)
                else:
                    # A turn that ran and cost nothing on the meter is the cap
                    # silently switched off; say so rather than skip it.
                    logger.warning(
                        "no usage found in a subscription message response "
                        "(content-encoding %r); the turn is not metered",
                        encoding,
                    )
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


_UPSTREAM_RESETS_AT = re.compile(r'"resets_at"\s*:\s*(\d+)')
_GATEWAY_MODEL_GROUP = re.compile(r"Received Model Group=(\S+)")
_BEIJING = timezone(timedelta(hours=8))


def _answer_a_reached_gateway_cap(flow: http.HTTPFlow) -> bool:
    """Re-render a gateway 429 that reports a spent allowance as a reached cap.

    LiteLLM's ``/v1/messages`` gives every 429 the same Anthropic envelope,
    ``error.type`` ``rate_limit_error``, whatever stopped the request; only
    ``error.message`` says which. Claude Code takes that for throttling, retries
    the turn ten times and prints "Server is temporarily limiting requests (not
    your usage limit)". Two of those 429s are not throttling:

    - LiteLLM's own ``max_budget`` brake ("Budget has been exceeded! Key=…"):
      the ledger learns a turn's spend only when the turn ends, so near the end
      of a budget this brake, not admission, stops the turn. Its message names
      the key's hash and is not passed on.
    - The subscription account behind a model has used up its plan (ChatGPT's
      ``usage_limit_reached``, carried inside the message with ``resets_at``).
      No retry within the turn can succeed; another model can.

    Any other 429 is real throttling and is passed on as it came.
    """
    resp = flow.response
    if resp is None or resp.status_code != 429:
        return False
    try:
        body = json.loads(resp.content or b"")
    except ValueError:
        return False
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return False
    said = error.get("message")
    said = said if isinstance(said, str) else ""
    if said.startswith("Budget has been exceeded"):
        _refuse_reached_cap(flow, "cheese project budget: 额度已用完。", None)
        return True
    if "usage_limit_reached" not in said:
        return False
    group = _GATEWAY_MODEL_GROUP.search(said)
    model = f"模型 {group.group(1)} " if group else "这个模型"
    resets = _UPSTREAM_RESETS_AT.search(said)
    reopens_at = int(resets.group(1)) if resets else None
    when = ""
    if reopens_at is not None:
        at = datetime.fromtimestamp(reopens_at, _BEIJING)
        when = f"，北京时间{at.month}月{at.day}日 {at:%H:%M} 恢复"
    _refuse_reached_cap(
        flow,
        f"cheese: {model}背后的订阅账号用量已到上限{when}。换一个模型可以继续。",
        reopens_at,
    )
    return True


def error(flow: http.HTTPFlow) -> None:
    attempt = ATTEMPTS.pop(flow.id, None)
    if attempt:
        CLAUDE_ACCOUNTS.release(attempt.account, flow.id)
        attempt.close()
    if flow.metadata.get("cheese_pool") == GATEWAY:
        _log_gateway_timing(flow)
    chatgpt_account = flow.metadata.get("cheese_chatgpt_account")
    via = getattr(flow.server_conn, "via", None)
    if chatgpt_account and via:
        logger.warning(
            "request for ChatGPT account %s through its egress %s:%s failed: %s",
            chatgpt_account,
            via[1][0],
            via[1][1],
            flow.error,
        )
    egress = flow.metadata.get("cheese_egress")
    if egress is not None:
        if "407" in str(flow.error):
            _REFUSED_EGRESS["egress"] = egress
        else:
            EGRESS_HEALTH.forget(egress)
        logger.warning(
            "request through the Claude credential's egress %s:%s failed: %s",
            egress.host,
            egress.port,
            flow.error,
        )


def response(flow: http.HTTPFlow) -> None:
    attempt = ATTEMPTS.pop(flow.id, None)
    if attempt:
        CLAUDE_ACCOUNTS.release(attempt.account, flow.id)
        attempt.close()
    if _answer_a_missed_binding(flow):
        return
    if flow.metadata.get("cheese_pool") == GATEWAY:
        _log_gateway_timing(flow)
        if _answer_a_reached_gateway_cap(flow):
            return
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
