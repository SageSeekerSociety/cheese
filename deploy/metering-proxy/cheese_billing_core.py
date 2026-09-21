"""Pure logic for the subscription metering proxy (stdlib only, no mitmproxy).

The addon (billing_addon.py) is mounted alone into a mitmproxy container, so
everything testable lives here where the backend's unit suite can load it by
path without dragging mitmproxy in. Three concerns:

- scoped-token verification: the proxy half of #198. The sandbox's Bearer is a
  cheese scoped token (HMAC over {p, t, exp}, minted by the backend); verifying
  it here means billing attribution comes from claims the sandbox cannot forge,
  and an exposed proxy stops being spendable by anyone who can reach it.
- admission: ask the backend whether the project can afford one more turn
  (POST /llm/admission, the caller's own Bearer). Fail-OPEN on transport
  errors — a broken brake must not be a broken platform — and cache verdicts
  briefly so a streaming session doesn't hammer the endpoint.
- metering: the rolling token window (deployment-wide backstop cap) and the
  durable JSONL trail the backend ingests (subscription_ingest).

Token algorithm mirrored from backend/app/core/sandbox_auth.py — HMAC-SHA256
over the urlsafe-b64 body, both unpadded. The secret is SANDBOX_TOKEN, shared
with the backend via env; rotate them together.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# The only names the proxy serves. api.anthropic.com carries the metered
# messages; console.anthropic.com and platform.claude.com carry interactive
# Claude Code's login/refresh. Everything else is refused (reverse listener) or
# tunneled raw without interception (CONNECT listener) — the real credential is
# injected per-request, so forwarding to an attacker-chosen SNI would hand the
# subscription token to whatever host the caller named.
ANTHROPIC_HOSTS = frozenset(
    {"api.anthropic.com", "console.anthropic.com", "platform.claude.com"}
)


def proxy_basic_password(header_value: str) -> str:
    """The password of a ``Proxy-Authorization: Basic`` header, else "".

    The CONNECT listener authenticates callers by the scoped cheese token their
    HTTPS_PROXY URL carries as the password (``http://cheese:<token>@host:port``
    — Claude Code sends it as Basic on every CONNECT, measured on 2.1.229).
    Malformed input of any shape yields "" rather than raising: this runs on
    attacker-controlled bytes."""
    parts = header_value.split()
    if len(parts) != 2 or parts[0].lower() != "basic":
        return ""
    try:
        decoded = base64.b64decode(parts[1], validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return ""
    _user, sep, password = decoded.partition(":")
    return password if sep else ""


def verify_scoped_token(
    token: str, secret: str, now: float | None = None
) -> dict | None:
    """The {p, t, exp} claims of a valid, unexpired scoped token, else None."""
    if not token or not secret or "." not in token:
        return None
    body, sig = token.split(".", 1)
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    want = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(sig, want):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or (now or time.time()) >= exp:
        return None
    return claims


class StreamingUsageExtractor:
    """Scrape a turn's usage/model out of an SSE response WITHOUT holding the
    whole body. Feed raw chunks as they pass through the proxy; only a single
    partial line is ever retained, so memory stays O(one SSE line) no matter how
    long the stream runs. Read ``.usage``/``.model`` after ``close()``.

    Anthropic puts input tokens on message_start and the output count on
    message_delta, so the usage is merged across events — neither alone is the
    turn's cost."""

    def __init__(self) -> None:
        self._buf = b""
        self.usage: dict = {}
        self.model = ""

    # A usage-bearing SSE line is well under 1 KiB; nothing we meter is remotely
    # this large. The cap only guarantees the O(one line) memory bound survives
    # malformed, newline-less input — a real line is never dropped by it.
    _MAX_LINE = 1 << 20

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._buf += chunk
        # Consume complete lines; keep the trailing partial for the next chunk.
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            self._consume(raw)
        if len(self._buf) > self._MAX_LINE:
            # No newline in a megabyte: not a usage event, and not worth holding.
            self._buf = b""

    def close(self) -> None:
        if self._buf:
            self._consume(self._buf)
            self._buf = b""

    def _consume(self, raw: bytes) -> None:
        if not raw.startswith(b"data: "):
            return
        try:
            evt = json.loads(raw[6:])
        except json.JSONDecodeError:
            return
        msg = evt.get("message") or {}
        self.model = self.model or msg.get("model", "")
        for src in (msg.get("usage"), evt.get("usage")):
            if isinstance(src, dict):
                self.usage.update(
                    {k: v for k, v in src.items() if isinstance(v, int)}
                )


def usage_from_sse(body: bytes) -> tuple[dict, str]:
    """Merge the usage block out of a fully-buffered streamed response. Kept for
    the non-streaming callers (and the unit suite); the live proxy path scrapes
    usage incrementally via StreamingUsageExtractor instead of buffering."""
    ex = StreamingUsageExtractor()
    ex.feed(body)
    ex.close()
    return ex.usage, ex.model


class Meter:
    """Rolling token total over a window, plus a durable JSONL trail."""

    def __init__(self, usage_log: Path, cap_window_s: int) -> None:
        self._lock = threading.Lock()
        self._window = cap_window_s
        self._log = usage_log
        self._events: list[tuple[float, int]] = []  # (ts, total_tokens)
        self._log.parent.mkdir(parents=True, exist_ok=True)
        self._restore()

    def _restore(self) -> None:
        """Survive a proxy restart — otherwise the cap resets to zero on crash,
        which is the one moment it most needs to hold."""
        if not self._log.exists():
            return
        cutoff = time.time() - self._window
        try:
            with self._log.open() as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("ts", 0) >= cutoff:
                        self._events.append((rec["ts"], rec.get("total_tokens", 0)))
        except OSError:
            pass

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        self._events = [e for e in self._events if e[0] >= cutoff]

    def used(self) -> int:
        with self._lock:
            self._prune(time.time())
            return sum(t for _, t in self._events)

    def would_exceed(self, cap: int) -> bool:
        return cap > 0 and self.used() >= cap

    def record(self, project_id: str, topic_id: str, usage: dict, model: str) -> None:
        total = (
            int(usage.get("input_tokens", 0))
            + int(usage.get("output_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0))
            + int(usage.get("cache_read_input_tokens", 0))
        )
        now = time.time()
        rec = {
            "ts": now,
            "project_id": project_id or None,
            "topic_id": topic_id or None,
            "model": model,
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0)),
            "cache_creation_input_tokens": int(
                usage.get("cache_creation_input_tokens", 0)
            ),
            "total_tokens": total,
            "provider": "subscription",
        }
        with self._lock:
            self._events.append((now, total))
            self._prune(now)
        with self._log.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")


SUBSCRIPTION = "subscription"
GATEWAY = "gateway"

# Which layer `reason` came from, and so how a refusal carrying it is rendered:
# a budget refusal is a 429 the client should back off from, a binding refusal
# is a 400 nobody should retry until the card changes.
BUDGET = "budget"
BINDING = "binding"


@dataclass(frozen=True)
class Verdict:
    """The backend's answer for one project: may it run, and where does it go.

    ``pool`` defaults to the subscription because that is the only destination
    a proxy older than the supply decision ever had — an admission response
    without a ``supply`` block must keep behaving exactly as before.
    """

    allow: bool
    reason: str
    # Which layer `reason` came from, so a refusal can be rendered as itself.
    # Every refusal used to become "cheese project budget: …" with a 429,
    # because a spent budget was the only way a turn was ever refused; a card
    # bound to a model the catalogue cannot serve is now the other way, and
    # telling that user their quota ran out points them at the one thing that
    # is fine. "budget" (the default) keeps a backend too old to say behaving
    # as before. Read only when `allow` is false; an allowed turn has a reason
    # too, and it says nothing anyone has to act on.
    reason_kind: str = BUDGET
    pool: str = SUBSCRIPTION
    key: str | None = None
    # The model name to write into the request body. It comes from the binding
    # on the card, resolved at admission — the launch environment names none, so
    # this is the only thing that says which model the turn runs on. Empty means
    # the backend did not say, and the client's own choice is forwarded.
    model: str = ""
    # `user:password` — which ccproxy identity to authenticate the upstream hop
    # as for this project's turns. Present only when the backend knows the
    # machine those turns run on; ccproxy scopes its fake→real ticket swap to
    # the authenticated connection, so this is what lets the machine's own
    # ticket be forwarded untouched instead of swapped for one the proxy holds.
    # None = fall back to the deployment-wide identity, and to the swap.
    upstream: str | None = None
    # True = NOBODY ANSWERED. This verdict was manufactured here — admission is
    # unconfigured, the project is unknown, or the backend could not be reached
    # — so every field on it is a default, `pool` included. Defaults to True so
    # that a verdict built anywhere but out of a real admission response says so
    # without having to remember to; `_post_admission` is the one place that
    # turns it off. A caller that reads `pool` to decide WHO MAY ANSWER (rather
    # than merely where to send a turn) must check this first: fail-open means
    # `pool` reads SUBSCRIPTION for a project that may well be on the gateway.
    fail_open: bool = True


def _post_admission(url: str, bearer: str, timeout_s: float) -> Verdict:
    """One admission call. Raises on transport problems (caller decides policy)."""
    req = urllib.request.Request(
        url, method="POST", headers={"Authorization": f"Bearer {bearer}"}, data=b""
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — fixed scheme/URL from deployment config
        payload = json.loads(resp.read())
    data = payload.get("data") or {}
    supply = data.get("supply") or {}
    pool = supply.get("pool")
    upstream = supply.get("upstream")
    model = supply.get("model")
    kind = data.get("reason_kind")
    return Verdict(
        allow=bool(data.get("allow", True)),
        reason=str(data.get("reason", "")),
        reason_kind=kind if kind in (BUDGET, BINDING) else BUDGET,
        pool=pool if pool in (SUBSCRIPTION, GATEWAY) else SUBSCRIPTION,
        key=supply.get("key") or None,
        model=model if isinstance(model, str) else "",
        # Shape-checked here rather than at use: a half credential ("m516:" or
        # ":pw") would authenticate as nobody, and failing at the parse names
        # the control plane as the source instead of surfacing as an upstream
        # 407 several hops away.
        upstream=upstream
        if isinstance(upstream, str) and len(upstream.split(":", 1)) == 2
        and all(upstream.split(":", 1))
        else None,
        # The backend answered; `pool` below is its word, not a default.
        fail_open=False,
    )


class AdmissionGate:
    """Per-project cached yes/no from the backend's /llm/admission.

    Fail-open by design: an unreachable backend logs and allows — the rolling
    token cap stays as the deployment-wide backstop, and a brake that can take
    the platform down is worse than the overspend it prevents. Verdicts cache
    for ``cache_s`` so a chatty session asks once, not per request.

    The same answer also carries the SUPPLY decision (#243): which pool serves
    this project. Fail-open therefore has a direction — an unreachable control
    plane falls back to the subscription, the destination this proxy has always
    had, rather than to a gateway whose per-project key it would not have.

    Cached per (project, topic), not per project. The budget half of the answer
    is the project's, but the ``upstream`` half names ONE machine — the one that
    topic's turns run on — and a project's topics can be spread over several. A
    project-wide key hands the second topic the first one's machine identity for
    the rest of the window, and ccproxy only honours a machine's ticket over that
    machine's own connection, so the turn either 401s at the far edge or is
    billed to the wrong machine. The extra key costs one admission call per topic
    per window, which is what the endpoint was already sized for.
    """

    def __init__(
        self,
        url: str,
        cache_s: float = 30.0,
        timeout_s: float = 3.0,
        post=_post_admission,
    ) -> None:
        self._url = url
        self._cache_s = cache_s
        self._timeout = timeout_s
        self._post = post  # test seam
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str, str], tuple[float, Verdict]] = {}

    def check(self, project_id: str, topic_id: str, bearer: str) -> Verdict:
        if not self._url or not project_id:
            return Verdict(True, "admission not configured")
        # A relaunched agent can select a different model supply in the same room.
        key = (project_id, topic_id, hashlib.sha256(bearer.encode()).hexdigest())
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self._cache_s:
                return hit[1]
        try:
            verdict = self._post(self._url, bearer, self._timeout)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return Verdict(True, "admission unreachable (fail-open)")
        with self._lock:
            # Drop what has expired instead of letting it pile up. Keyed by
            # project alone this was one entry per project and effectively
            # bounded; keyed by topic it is one per topic ever served, which on a
            # box carrying hundreds of them is a slow leak — in a process that
            # has already been OOM-killed once (#654). The sweep is O(entries
            # still inside the window) and only runs on a miss.
            cutoff = now - self._cache_s
            self._cache = {k: v for k, v in self._cache.items() if v[0] >= cutoff}
            self._cache[key] = (now, verdict)
        return verdict


# --- Claude Code's non-model startup endpoints -------------------------------
#
# 一个控制点（结论 46）。A machine is launched in one shape — no base URL, this
# proxy on HTTPS_PROXY, a fake ticket — and that shape has to boot Claude Code
# on a deployment that owns no Anthropic subscription at all. Claude Code asks
# for four things on its way up that have nothing to do with inference: who am
# I, what are my settings, what is my policy, and here is my telemetry. Every
# one of them is answered HERE.
#
# Answering them locally is not merely convenient. The upstream reply would name
# the PLATFORM'S subscription account — its uuid, its email, its org — to a
# sandbox running someone else's code, whichever pool that sandbox runs on. One
# echo of an identity that has already left the building is not recoverable.
#
# The table is the contract: a path, a status and the fields the client must
# find. Tests assert it entry by entry, and a boot that asks for a path absent
# from it is a boot that reached upstream — which is what the acceptance run
# watches for.

_PROFILE = "/api/oauth/profile"
_SETTINGS = "/api/claude_code/settings"
_POLICY = "/api/claude_code/policy_limits"
# Feature evaluation. Cheese supplies its own flags; the three below are what
# turn on the remote-control bridge the platform drives every session through.
_FLAGS_PREFIX = "/api/eval/"
# Telemetry. RC payloads carry control-session identifiers, so neither they nor
# an upstream credential may cross this boundary.
_TELEMETRY_PREFIX = "/api/event_logging/"
TELEMETRY_HOSTS = frozenset({"api.statsig.com", "statsig.anthropic.com"})

RC_FLAGS = {
    "tengu_ccr_bridge": True,
    "tengu_ccr_v2_bridge_create_cli": True,
    "tengu_ccr_v2_session_crud_cli": True,
}


@dataclass(frozen=True)
class Answer:
    """One row of the table: what the proxy sends back, and nothing upstream."""

    status: int
    body: bytes


def control_answer(
    host: str, path: str, project: str, topic: str, rc: bool = False
) -> Answer | None:
    """The proxy's own answer for a non-model endpoint, or None to forward.

    ``project``/``topic`` are the VERIFIED place from the caller's scoped token:
    the identity Cheese asserts is Cheese's own, never an Anthropic account's.

    ``rc`` says whether that token grants this session the Cheese RC transport.
    Every path below is answered either way — the point is that none of them
    reaches Anthropic — but only an RC session is told the bridge is on, because
    those flags are what make Claude Code open `/v1/code/…`, and a session
    without the claim has no RC route for them to take.
    """
    path = path.split("?", 1)[0]
    if path == _PROFILE:
        return Answer(
            200,
            json.dumps(
                {
                    "account": {"uuid": topic, "email": "cheese@agent.cheese.local"},
                    "organization": {"uuid": project},
                }
            ).encode(),
        )
    if path == _SETTINGS:
        return Answer(204, b"")
    if path == _POLICY:
        return Answer(
            200,
            json.dumps(
                {"restrictions": {"allow_remote_control": {"allowed": True}}}
            ).encode(),
        )
    if path.startswith(_FLAGS_PREFIX):
        return Answer(
            200,
            json.dumps(
                {
                    "features": {
                        name: {"defaultValue": value}
                        for name, value in (RC_FLAGS if rc else {}).items()
                    }
                }
            ).encode(),
        )
    if path.startswith(_TELEMETRY_PREFIX) or host in TELEMETRY_HOSTS:
        return Answer(200, b"{}")
    return None


class ModelRewrite:
    """Write the admitted model name into a streamed ``/v1/messages`` body.

    The launch environment names no model any more (结论 46): the binding on the
    card is resolved at admission, and this is where the answer reaches the one
    place either pool reads a model from. LiteLLM reads it because a subagent
    would otherwise ask it for ``claude-3-5-haiku-*``, which it does not serve;
    the subscription reads it because a project bound to opus would otherwise
    run on whatever family the CLI defaults to.

    Streamed, not buffered. A long turn re-POSTs its whole grown conversation
    every time, and holding that in RAM is what OOM-killed this proxy (#654). So
    only the head is held — until the top-level ``model`` member has gone past,
    or ``limit`` bytes have, whichever comes first — and everything after it is
    forwarded chunk for chunk.

    Parsed, not searched. ``"model":`` also occurs inside message text, and a
    naive first-match would rewrite a user's own words. This tracks string and
    escape state and only accepts the member at depth 1 of the top-level object.

    ``limit`` is 64KiB and that number is NOT yet backed by a captured request:
    where the client serializes its top-level ``model`` member is the client's
    choice, and Claude Code's ``tools`` + ``system`` alone run to tens of KB, so
    a body that puts ``model`` after ``messages`` would miss on every turn of a
    long conversation. A miss is loud (`missed`, and the caller logs it) but it
    is not free: on the subscription the turn falls back to the CLI's own
    choice, and on the gateway it fails outright because LiteLLM does not serve
    the Claude names. Raise it against a real capture, not by guesswork — the
    buffer is held in RAM and unbounded buffering is the #654 OOM.
    """

    def __init__(
        self, model: str, limit: int = 1 << 16, keep_haiku: bool = False
    ) -> None:
        self._model = model
        self._limit = limit
        self._keep_haiku = keep_haiku
        self._buf = b""
        self._done = False
        # True once the head went past without a top-level `model` member. The
        # body is forwarded unchanged — the caller logs it, because it means the
        # turn runs on whatever the client asked for rather than on the binding.
        self.missed = False

    def feed(self, chunk: bytes) -> bytes:
        """One chunk in, the chunk to forward out. ``b""`` ends the stream."""
        if self._done:
            return chunk
        if not chunk:
            # End of stream with the head still held: nothing more is coming.
            self._done = True
            self.missed = True
            out, self._buf = self._buf, b""
            return out
        self._buf += chunk
        span = top_level_model_span(self._buf)
        if span is not None:
            start, end = span
            if self._keep_haiku and _is_haiku(self._buf[start:end]):
                out = self._buf
            else:
                out = (
                    self._buf[:start]
                    + json.dumps(self._model).encode()
                    + self._buf[end:]
                )
            self._done = True
            self._buf = b""
            return out
        if len(self._buf) >= self._limit:
            self._done = True
            self.missed = True
            out, self._buf = self._buf, b""
            return out
        return b""


def _is_haiku(value: bytes) -> bool:
    """Did the CLI address this request to the small, fast family?

    Claude Code sends more than the conversation: it names the haiku family for
    session titles, file-path suggestions and other background work it does on
    its own account. On the SUBSCRIPTION pool those requests kept going to haiku
    before the launch environment stopped naming a model — only the main reply
    ever carried the binding — and rewriting them to the bound model means a
    project on opus generates its session titles on opus, for work nobody bound
    to anything.

    The gateway pool is the opposite case and does not pass this flag: LiteLLM
    does not serve `claude-3-5-haiku-*` at all, so a request left naming it
    fails outright.
    """
    return b"haiku" in value.lower()


def top_level_model_span(data: bytes) -> tuple[int, int] | None:
    """Byte span of the top-level ``model`` member's VALUE, once it is complete.

    None means "not in this prefix yet" — either the member has not appeared or
    its closing quote has not arrived. Depth and string state are tracked so a
    ``"model":`` inside message text, or inside a nested object, is not it.
    """
    depth = 0
    i = 0
    n = len(data)
    while i < n:
        c = data[i : i + 1]
        if c == b'"':
            end = _string_end(data, i)
            if end is None:
                return None
            if depth == 1 and data[i:end] == b'"model"':
                j = _skip_space(data, end)
                if j >= n or data[j : j + 1] != b":":
                    return None
                j = _skip_space(data, j + 1)
                if j >= n:
                    return None
                if data[j : j + 1] != b'"':
                    # A non-string value is not a model name; leave it alone.
                    i = end
                    continue
                value_end = _string_end(data, j)
                if value_end is None:
                    return None
                return j, value_end
            i = end
            continue
        if c in (b"{", b"["):
            depth += 1
        elif c in (b"}", b"]"):
            depth -= 1
        i += 1
    return None


def _skip_space(data: bytes, i: int) -> int:
    while i < len(data) and data[i : i + 1] in (b" ", b"\t", b"\r", b"\n"):
        i += 1
    return i


def _string_end(data: bytes, start: int) -> int | None:
    """Index just past the closing quote of the JSON string at ``start``."""
    i = start + 1
    n = len(data)
    while i < n:
        c = data[i : i + 1]
        if c == b"\\":
            i += 2
            continue
        if c == b'"':
            return i + 1
        i += 1
    return None
