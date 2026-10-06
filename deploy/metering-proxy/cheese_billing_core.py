"""Pure logic for the subscription metering proxy (stdlib only, no mitmproxy).

The addon (billing_addon.py) is mounted alone into a mitmproxy container, so
everything testable lives here where the backend's unit suite can load it by
path without dragging mitmproxy in. Three concerns:

- scoped-token verification: the proxy half of #198. The sandbox's Bearer is a
  cheese scoped token (HMAC over {p, t, exp}, minted by the backend); verifying
  it here means billing attribution comes from claims the sandbox cannot forge,
  and an exposed proxy stops being spendable by anyone who can reach it.
- admission: ask the backend whether the project can afford one more turn
  (POST /llm/admission, the caller's own Bearer naming the room, and this
  proxy's own credential, without which the answer carries no gateway key).
  Fail-OPEN on transport
  errors — a broken brake must not be a broken platform — and cache verdicts
  briefly so a streaming session doesn't hammer the endpoint.
- metering: the rolling token window (deployment-wide backstop cap) and the
  durable JSONL trail the backend ingests (subscription_ingest).

Token algorithm mirrored from backend/app/core/sandbox_auth.py — HMAC-SHA256
over the urlsafe-b64 body, both unpadded. The secret is SANDBOX_TOKEN, shared
with the backend via env; rotate them together.
"""

import base64
import gzip
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, replace
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

# What every session carries as its Claude login: enough for Claude Code to
# boot, and nothing that authenticates. This proxy replaces it with the
# platform's credential (PlatformCredential) or, when there is none, answers or
# refuses for it. The launch script writes the same value
# (backend/app/domain/agent/harness/claude_code/device_launch.py).
NO_LOGIN_PLACEHOLDER = "sk-ant-oat01-cheese-no-claude-login-on-this-host"

# The non-model endpoints Claude Code calls at boot that only a real account can
# answer, answered here when the platform has no credential, instead of a 503.
# None of them gates a turn (measured on 2.1.277: a turn completes with all
# three refused), so this only keeps refusals out of every boot; each response
# schema is all-optional in the client. With a credential they go to Anthropic.
NO_LOGIN_ANSWERS = {
    "/api/claude_cli/bootstrap": b"{}",
    "/api/claude_code_penguin_mode": b'{"enabled": false}',
    "/api/oauth/validate": b"{}",
}


def no_login_answer(host: str, path: str) -> bytes | None:
    """The local answer for a placeholder session's boot call, else None."""
    if host != "api.anthropic.com":
        return None
    return NO_LOGIN_ANSWERS.get(path.split("?", 1)[0])


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


def _decoder(encoding: str):
    """An incremental decoder for a response's Content-Encoding, or None when
    this proxy cannot read it. The bytes forwarded to the client are never
    touched; only the copy read for usage is decoded."""
    encoding = encoding.strip().lower()
    if encoding in ("", "identity"):
        return lambda chunk: chunk
    if encoding in ("gzip", "x-gzip", "deflate"):
        # 47: zlib or gzip framing, detected from the header.
        return zlib.decompressobj(47).decompress
    if encoding == "br":
        import brotli  # in the proxy image, not in the backend's test env

        return brotli.Decompressor().process
    if encoding == "zstd":
        import zstandard  # in the proxy image, not in the backend's test env

        return zstandard.ZstdDecompressor().decompressobj().decompress
    return None


class StreamingUsageExtractor:
    """Scrape a turn's usage/model out of an SSE response WITHOUT holding the
    whole body. Feed raw chunks as they pass through the proxy; only a single
    partial line is ever retained, so memory stays O(one SSE line) no matter how
    long the stream runs. Read ``.usage``/``.model`` after ``close()``.

    Anthropic puts input tokens on message_start and the output count on
    message_delta, so the usage is merged across events — neither alone is the
    turn's cost.

    ``encoding`` is the response's Content-Encoding. Anthropic compresses the
    SSE of a client that accepts it, as Claude Code does, and read raw those
    bytes hold no event at all: usage stayed empty and nothing was recorded."""

    def __init__(self, encoding: str = "") -> None:
        self._buf = b""
        self.usage: dict = {}
        self.model = ""
        self._decode = _decoder(encoding)

    # A usage-bearing SSE line is well under 1 KiB; nothing we meter is remotely
    # this large. The cap only guarantees the O(one line) memory bound survives
    # malformed, newline-less input — a real line is never dropped by it.
    _MAX_LINE = 1 << 20

    def feed(self, chunk: bytes) -> None:
        if not chunk or self._decode is None:
            return
        chunk = self._decode(chunk)
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
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(evt, dict):
            return
        msg = evt.get("message")
        if not isinstance(msg, dict):
            msg = {}
        model = msg.get("model")
        if not self.model and isinstance(model, str):
            self.model = model
        for src in (msg.get("usage"), evt.get("usage")):
            if not isinstance(src, dict):
                continue
            for key, value in src.items():
                if type(value) is int and value >= 0:
                    self.usage[key] = value
                elif key == "cache_creation" and isinstance(value, dict):
                    # The cache writes split by lifetime; the 1-hour ones are
                    # billed at a higher rate than the 5-minute ones.
                    self.usage[key] = {
                        k: v for k, v in value.items() if type(v) is int and v >= 0
                    }


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
        split = usage.get("cache_creation")
        if isinstance(split, dict):
            # Written only when the response split its cache writes by
            # lifetime, so a reader can tell "all 5-minute" from "not said".
            rec["cache_creation_5m_input_tokens"] = int(
                split.get("ephemeral_5m_input_tokens", 0)
            )
            rec["cache_creation_1h_input_tokens"] = int(
                split.get("ephemeral_1h_input_tokens", 0)
            )
        with self._lock:
            self._events.append((now, total))
            self._prune(now)
        with self._log.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")


SUBSCRIPTION = "subscription"
GATEWAY = "gateway"

# Which layer `reason` came from, and so how a refusal carrying it is rendered:
# a budget refusal is a 429 that tells the client the cap is reached and not to
# retry into it, a binding refusal is a 400 nobody should retry until the card
# changes.
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
    # When a budget refusal stops applying, in Unix seconds; None when nothing
    # will change it on its own (the credits are spent, not a window full).
    reopens_at: int | None = None
    # True = NOBODY ANSWERED. This verdict was manufactured here — admission is
    # unconfigured, the project is unknown, or the backend could not be reached
    # — so every field on it is a default, `pool` included. Defaults to True so
    # that a verdict built anywhere but out of a real admission response says so
    # without having to remember to; `_post_admission` is the one place that
    # turns it off. A caller that reads `pool` to decide WHO MAY ANSWER (rather
    # than merely where to send a turn) must check this first: fail-open means
    # `pool` reads SUBSCRIPTION for a project that may well be on the gateway.
    fail_open: bool = True


def _post_admission(
    url: str,
    bearer: str,
    timeout_s: float,
    *,
    credential: str = "",
    subagent: bool = False,
    requested_model: str = "",
    child_model: str = "",
) -> Verdict:
    """One admission call. Raises on transport problems (caller decides policy).

    `bearer` is the session's scoped token: it names the room being admitted.
    `credential` is this proxy's own (the backend's SANDBOX_TOKEN): the backend
    hands a gateway project's key only to a caller presenting it, because a
    session holds the same bearer and must never hold the key."""
    headers = {"Authorization": f"Bearer {bearer}"}
    if credential:
        headers["X-Cheese-Token"] = credential
    if subagent:
        headers["X-Cheese-Subagent"] = "1"
        if child_model:
            headers["X-Cheese-Child-Model"] = child_model
        if requested_model:
            # 主 agent 开这个分身时指定的模型（从请求体顶层 model 成员读出的原
            # 样）。准入拿它决定「绑它」还是「拒绝并列出可选」——不带就是沿用
            # 项目的分身默认。
            headers["X-Cheese-Requested-Model"] = requested_model
    req = urllib.request.Request(url, method="POST", headers=headers, data=b"")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — fixed scheme/URL from deployment config
        payload = json.loads(resp.read())
    data = payload.get("data") or {}
    supply = data.get("supply") or {}
    pool = supply.get("pool")
    model = supply.get("model")
    kind = data.get("reason_kind")
    reopens_at = data.get("reopens_at")
    return Verdict(
        allow=bool(data.get("allow", True)),
        reason=str(data.get("reason", "")),
        reason_kind=kind if kind in (BUDGET, BINDING) else BUDGET,
        pool=pool if pool in (SUBSCRIPTION, GATEWAY) else SUBSCRIPTION,
        key=supply.get("key") or None,
        model=model if isinstance(model, str) else "",
        reopens_at=reopens_at if isinstance(reopens_at, int) else None,
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
    this project and which model goes into the body. Fail-open therefore has a
    direction. A session the backend has answered for within ``stale_s`` keeps
    the pool, key and model of that last answer: a session bound to a gateway
    model carries that model's name in its body, and sending it to the
    subscription instead gets a 404 model_not_found, which Claude Code does not
    retry, so every backend restart killed the gateway turns and subagents that
    were mid-request. A session with no answer on record falls back to the
    subscription, the destination this proxy has always had, rather than to a
    gateway whose per-project key it would not have.

    Cached per (project, topic), not per project. The budget half of the answer
    is the project's, but the model it binds comes from the topic's card, and a
    project-wide key would hand the second topic the first one's binding for the
    rest of the window. The extra key costs one admission call per topic per
    window, which is what the endpoint was already sized for.
    """

    def __init__(
        self,
        url: str,
        cache_s: float = 30.0,
        timeout_s: float = 3.0,
        post=_post_admission,
        stale_s: float = 3600.0,
        credential: str = "",
    ) -> None:
        self._url = url
        self._credential = credential
        self._cache_s = cache_s
        self._stale_s = stale_s
        self._timeout = timeout_s
        self._post = post  # test seam
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str, str, bool, str], tuple[float, Verdict]] = {}

    def check(
        self,
        project_id: str,
        topic_id: str,
        bearer: str,
        *,
        subagent: bool = False,
        requested_model: str = "",
        child_model: str = "",
    ) -> Verdict:
        if not self._url or not project_id:
            return Verdict(True, "admission not configured")
        # A relaunched agent can select a different model supply in the same room;
        # two subagents of one room can name two different requested models.
        key = (
            project_id,
            topic_id,
            hashlib.sha256(bearer.encode()).hexdigest(),
            subagent,
            requested_model if subagent else "",
            child_model if subagent else "",
        )
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self._cache_s:
                return hit[1]
        try:
            asked = (
                {"subagent": True, "child_model": child_model}
                if subagent and child_model
                else {"subagent": True, "requested_model": requested_model}
                if subagent
                else {}
            )
            verdict = self._post(
                self._url,
                bearer,
                self._timeout,
                credential=self._credential,
                **asked,
            )
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            if hit and now - hit[0] < self._stale_s:
                # Still fail-open: allowed whatever the last answer said about
                # the budget, but routed where the backend last routed it.
                return replace(
                    hit[1], allow=True, reason="admission unreachable (fail-open)"
                )
            return Verdict(True, "admission unreachable (fail-open)")
        with self._lock:
            # Drop what is too old to route by instead of letting it pile up.
            # Keyed by project alone this was one entry per project and
            # effectively bounded; keyed by topic it is one per topic ever
            # served, which on a box carrying hundreds of them is a slow leak —
            # in a process that has already been OOM-killed once (#654). The
            # sweep is O(entries still inside the horizon) and only runs on a
            # miss.
            cutoff = now - self._stale_s
            self._cache = {k: v for k, v in self._cache.items() if v[0] >= cutoff}
            self._cache[key] = (now, verdict)
        return verdict


# --- Claude Code's non-model startup endpoints -------------------------------
#
# 一个控制点（结论 46）。A machine is launched in one shape — no base URL, this
# proxy on HTTPS_PROXY — and that shape has to boot Claude Code
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
# find. It lives in control_answers.json rather than in this file because it has
# a second reader — cli/e2e scripts a stand-in Anthropic API from the same rows
# and boots a REAL Claude Code against them, then fails on any non-model path
# that real client asked for and this table did not answer 2xx. A row missing
# here is a request that goes upstream on the session's credential, and that
# test is what finds one before a deployment does.

TABLE_PATH = Path(__file__).resolve().with_name("control_answers.json")
_TABLE = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

_ROWS: list[dict] = _TABLE["rows"]
# The host a row serves when it names none. Three names are MITM'd here and
# only one of them carries a sandbox's boot: console.anthropic.com and
# platform.claude.com carry interactive Claude Code's login and refresh, which
# ask for these very paths — `/api/oauth/profile` above all — and must get
# ANTHROPIC'S answer. Answering those from this table hands `claude /login` a
# synthesised Cheese account, and the setup-token that IS this proxy's
# subscription credential is what that login exists to produce.
DEFAULT_ANSWER_HOSTS = frozenset({"api.anthropic.com"})
# How much of a /v1/messages head ModelRewrite may hold while it looks for the
# top-level `model` member. See ModelRewrite.
#
# Measured, not guessed: Claude Code 2.1.276 (`claude -p`, first turn, probe in
# `tmp/refactor-research-2026-09-19/impl/probe-model-offset.py`) sent a 245,909-
# byte body with `"model"` at byte 1 — the CLI writes it first, before
# `messages`, `system` and `tools`. 64 KiB is therefore a margin of four
# orders of magnitude, not a bet on where the member sits.
MODEL_REWRITE_LIMIT = int(_TABLE["model_rewrite_limit_bytes"])


@dataclass(frozen=True)
class Answer:
    """One row of the table: what the proxy sends back, and nothing upstream."""

    status: int
    body: bytes


def control_answer(host: str, path: str, project: str, topic: str) -> Answer | None:
    """The proxy's own answer for a non-model endpoint, or None to forward.

    ``host`` is part of the question, not decoration: a row answers only the
    hosts it names, defaulting to the one host a sandbox boots against. The
    login hosts ask for the same paths on their way to a real Anthropic account
    and go upstream untouched.

    ``project``/``topic`` are the VERIFIED place from the caller's scoped token:
    the identity Cheese asserts is Cheese's own, never an Anthropic account's.
    """
    path = path.split("?", 1)[0]
    for row in _ROWS:
        if not _row_matches(row, host, path):
            continue
        body = row["body"]
        if body is None:
            return Answer(row["status"], b"")
        return Answer(row["status"], json.dumps(_fill(body, project, topic)).encode())
    return None


def _row_matches(row: dict, host: str, path: str) -> bool:
    """Host first, for every kind of row — a path alone is not a match. The same
    path means one thing on the boot host and the opposite on the login hosts,
    and a row that forgets to say which it meant defaults to the boot host."""
    if host not in row.get("hosts", DEFAULT_ANSWER_HOSTS):
        return False
    if "exact" in row:
        return path == row["exact"]
    if "prefix" in row:
        return path.startswith(row["prefix"])
    return True


def _fill(value, project: str, topic: str):
    """Substitute the table's placeholders with this caller's own facts."""
    if isinstance(value, dict):
        return {k: _fill(v, project, topic) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, project, topic) for v in value]
    if value == "{project}":
        return project
    if value == "{topic}":
        return topic
    return value


_CODEX_STRING_END = re.compile(rb'["\\]')
_CODEX_STRUCTURE = re.compile(rb'["{}\[\],]')


class CodexBody:
    """Reshape a streamed Responses body into what ChatGPT's Codex backend takes.

    The backend refuses a request that does not say ``store: false`` ("Store
    must be set to false") and one that carries ``max_output_tokens``, ``user``
    or ``temperature`` ("Unsupported parameter"). The gateway's translation from
    Anthropic's Messages never writes the first, always writes
    ``max_output_tokens``, writes ``user`` whenever the caller sent
    ``metadata.user_id``, and passes ``temperature`` on whenever the caller set
    one — Claude Code does on its own small requests, such as reading a web
    page. So the body goes out opening with ``"store":false``, and any
    top-level ``store``, ``max_output_tokens``, ``user`` or ``temperature``
    member of its own is left out.

    Streamed, never buffered (see `_write_bound_model` for why that matters):
    only a member's key is held, while the member it opens is decided on; its
    value, however large, passes through as it arrives. A body that does not
    start with an object passes untouched.
    """

    DROPPED = frozenset({"store", "max_output_tokens", "user", "temperature"})
    _OPENING = b'{"store":false'

    def __init__(self) -> None:
        # before: ahead of the opening brace. key: between members, or inside a
        # member's key. value: inside a member's value. done: past the object.
        self._state = "before"
        self._key = b""  # the key being read, quotes included
        self._keep = True
        self._depth = 0  # nesting inside the current value
        self._in_string = False
        self._escaped = False

    def feed(self, chunk: bytes) -> bytes:
        """The next piece of the body; ``b""`` marks its end."""
        if self._state == "done":
            return chunk
        out = bytearray()
        i, n = 0, len(chunk)
        while i < n:
            if self._state == "before":
                while i < n and chunk[i : i + 1].isspace():
                    out += chunk[i : i + 1]
                    i += 1
                if i == n:
                    break
                if chunk[i : i + 1] != b"{":
                    self._state = "done"
                    return bytes(out) + chunk[i:]
                out += self._OPENING
                i += 1
                self._state = "key"
            elif self._state == "key":
                if self._key:  # inside the key's string
                    j = self._string_end(chunk, i)
                    self._key += chunk[i:j]
                    i = j
                    if self._in_string:
                        continue
                    name = json.loads(self._key)
                    self._keep = name not in self.DROPPED
                    if self._keep:
                        out += b"," + self._key
                    self._key = b""
                    self._state = "value"
                    self._depth = 0
                    continue
                c = chunk[i : i + 1]
                i += 1
                if c == b'"':
                    self._key = c
                    self._in_string = True
                elif c == b"}":
                    out += b"}"
                    self._state = "done"
                    return bytes(out) + chunk[i:]
                # whitespace and the comma between members are rewritten, not kept
            else:  # value
                if self._in_string:
                    j = self._string_end(chunk, i)
                else:
                    m = _CODEX_STRUCTURE.search(chunk, i)
                    j = m.start() if m else n
                if self._keep:
                    out += chunk[i:j]
                i = j
                if i == n or self._in_string:
                    continue
                c = chunk[i : i + 1]
                i += 1
                if c == b'"':
                    self._in_string = True
                elif c in b"{[":
                    self._depth += 1
                elif c in b"}]":
                    if self._depth == 0:  # the object's own closing brace
                        out += b"}"
                        self._state = "done"
                        return bytes(out) + chunk[i:]
                    self._depth -= 1
                elif c == b"," and self._depth == 0:
                    self._state = "key"
                    continue
                if self._keep:
                    out += c
        return bytes(out)

    def _string_end(self, chunk: bytes, i: int) -> int:
        """Advance through a string's bytes; clears `_in_string` at its close
        and returns the index just past what was consumed."""
        n = len(chunk)
        while i < n:
            if self._escaped:
                self._escaped = False
                i += 1
                continue
            m = _CODEX_STRING_END.search(chunk, i)
            if not m:
                return n
            i = m.start() + 1
            if chunk[m.start() : i] == b"\\":
                self._escaped = True
            else:
                self._in_string = False
                return i
        return n


class ModelRewrite:
    """Write the admitted model name into a streamed ``/v1/messages`` body.

    The binding on the card is resolved at admission (结论 46), and this is
    where the answer reaches the one place either pool reads a model from. The
    model Claude Code was launched with only shapes its own prompt, and can lag
    a binding changed since. LiteLLM reads it because a subagent
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

    ``limit`` is the head budget from control_answers.json, and cli/e2e asserts
    it against a REAL Claude Code request: the recorded body's top-level
    ``model`` member has to arrive inside it, ahead of the bulk members. Where a
    client serializes that member is the client's choice, and Claude Code's
    ``tools`` + ``system`` alone run to tens of KB — so a client that moved it
    after ``messages`` would miss on every turn of a long conversation, and that
    is a change the e2e catches rather than something to guess a bigger number
    against. The buffer is held in RAM; unbounded buffering is the #654 OOM.

    A miss REFUSES the turn (I27): nothing is forwarded, and the caller turns
    the upstream's answer into a refusal that names the reason. Forwarding the
    head unchanged — which is what this used to do — ran the turn on whatever
    model the CLI had picked for itself: a silent swap of the brain on the
    subscription, and on the gateway a hard failure LiteLLM reports as its own.
    """

    def __init__(
        self, model: str, limit: int = MODEL_REWRITE_LIMIT, keep_haiku: bool = False
    ) -> None:
        self._model = model
        self._limit = limit
        self._keep_haiku = keep_haiku
        self._buf = b""
        self._done = False
        # True once the head went past without a top-level `model` member. The
        # body is then DROPPED rather than forwarded: this turn cannot be put on
        # the model the card is bound to, and the exits are refuse or wait, not
        # run it on something else (I27). The caller reports the reason.
        self.missed = False

    def feed(self, chunk: bytes) -> bytes:
        """One chunk in, the chunk to forward out. ``b""`` ends the stream."""
        if self.missed:
            # Refused. Nothing more of this body goes upstream, whatever else
            # the client is still sending.
            return b""
        if self._done:
            return chunk
        if not chunk:
            # End of stream with the head still held: nothing more is coming.
            self._buf = b""
            self.missed = True
            return b""
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
            # The memory bound still holds: the head is dropped, not kept.
            self._buf = b""
            self.missed = True
            return b""
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
                if j >= n:
                    # The bytes after it have not arrived; ask again next chunk.
                    return None
                if data[j : j + 1] != b":":
                    # A top-level string whose CONTENT is "model" — a value, not
                    # a key. Giving up here would give up for good: every later
                    # chunk rescans from the start and lands on the same string,
                    # so the whole body would miss and the turn be refused for a
                    # member that is sitting further along.
                    i = end
                    continue
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


def requested_model_of(body: bytes) -> str:
    """请求体顶层 ``model`` 成员的原样值 —— 主 agent 给这个分身指定的那一个。

    与 ``ModelRewrite`` 共用同一个解析器：读到的是 CC 写进请求体的指定，不是
    改写后要绑的那个。成员缺席或不是字符串时为空串，调用方按「未指定」处理
    （沿用项目的分身默认）。
    """
    span = top_level_model_span(body)
    if span is None:
        return ""
    try:
        value = json.loads(body[span[0] : span[1]])
    except ValueError:
        return ""
    if not isinstance(value, str):
        return ""
    # 这个名字要进准入门（X-Cheese-Requested-Model 请求头）。原文照塞会让一
    # 个带控制字符或非 Latin-1 的名字在 putheader 抛 ValueError，被 check()
    # 当成传输故障 fail-open —— 准入拿不到名字,缓冲改写退成 no-op,请求体原
    # 样上行。那是把客户端字符串放进准入门才新增的触发面,所以不合格的按未
    # 指定处理:准入照常绑定,改写照样盖掉体里那个值。
    if not _MODEL_NAME_RE.fullmatch(value):
        return ""
    return value


# 目录里的模型名都落在这组字符里（claude-sonnet-5、glm-4.6、openai/gpt-5）。
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9._:/-]{1,128}")


def is_haiku_name(value: str) -> bool:
    """是否属于小快家族 —— CLI 自己的后台请求类(会话标题、路径建议)。

    与 ``_is_haiku`` 同一个判定,给请求体里读出来的名字用。这类请求从来不算
    「指定模型」:主对话路径上它们留在 haiku(keep_haiku),分身路径上它们照旧
    绑分身默认 —— 两条路都不进白名单校验。
    """
    return _is_haiku(value.encode())


# --- the platform's Claude credential ----------------------------------------
#
# Sessions carry NO_LOGIN_PLACEHOLDER; this proxy is the only holder of the real
# credential and puts it on each request on its way to Anthropic. The file holds
# one of two shapes:
#   - a bare token string: a one-year `claude setup-token`. It never rotates, so
#     it is used as it is and nothing here writes the file.
#   - a Claude Code `.credentials.json` document ({"claudeAiOauth": {...}}): an
#     access token that lives ~8 hours plus the refresh token that renews it.
#     This process refreshes it itself and writes the new pair back. The refresh
#     token's own deadline (~30 days from login) does not move on refresh; once
#     it passes, someone logs in again.
# The file is re-read on every request, so writing, replacing or deleting it is
# logging in, switching account or logging out, for every running session at
# once. Only one holder may ever refresh a pair, because a refresh rotates it:
# the login script moves the pair here and deletes the copy it was made in.
#
# The refresh request is byte for byte the one the pinned Claude Code sends
# (`refresh_request`): URL, client id, JSON body in the same key order, scope
# list, and its headers with their names, values and order.
# scripts/remote_execution/refresh_contract.py captures the pinned build's own
# refresh and fails when the two differ, so an upgrade that changes it is caught
# before it ships.
OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_REFRESH_SCOPES = (
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
    "user:plugins",
)
# Claude Code refreshes when the access token is within five minutes of expiry.
REFRESH_MARGIN_S = 300
# After a failed refresh that was not a refusal, wait before asking again: the
# access token may still be valid, and every request would otherwise retry.
REFRESH_BACKOFF_S = 60
# Claude Code starts warning three days before the refresh token expires.
LOGIN_WARNING_S = 3 * 86400

# Why there is no credential to put on a request.
NO_CREDENTIAL = "none"
EXPIRED = "expired"
LOGIN_REQUIRED = "login_required"

_credential_log = logging.getLogger("cheese.metering")


def refresh_request(body: dict) -> tuple[list[tuple[str, str]], bytes]:
    """The refresh POST's headers, in the order they are sent, and its body."""
    raw = json.dumps(body, separators=(",", ":")).encode()
    headers = [
        ("Accept", "application/json, text/plain, */*"),
        ("Content-Type", "application/json"),
        ("User-Agent", "axios/1.15.2"),
        ("Content-Length", str(len(raw))),
        ("Accept-Encoding", "gzip, compress, deflate, br"),
        ("Host", urllib.parse.urlsplit(OAUTH_TOKEN_URL).hostname or ""),
        ("Connection", "close"),
    ]
    return headers, raw


def _decoded(raw: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(raw)
    if encoding == "deflate":
        return zlib.decompress(raw)
    if encoding == "br":
        try:
            import brotli  # mitmproxy's own dependency, present in the image
        except ImportError as err:
            raise OSError("a brotli response and no brotli module") from err
        return brotli.decompress(raw)
    return raw


@dataclass(frozen=True)
class Egress:
    """The HTTP proxy a credential's requests to Anthropic leave through."""

    host: str
    port: int
    # The value of the Proxy-Authorization header, "" when the proxy asks none.
    authorization: str = ""

    @classmethod
    def parse(cls, url: str) -> "Egress | None":
        parts = urllib.parse.urlsplit(url.strip())
        if parts.scheme != "http" or not parts.hostname or not parts.port:
            return None
        authorization = ""
        if parts.username is not None:
            pair = (
                f"{urllib.parse.unquote(parts.username)}:"
                f"{urllib.parse.unquote(parts.password or '')}"
            )
            authorization = "Basic " + base64.b64encode(pair.encode()).decode()
        return cls(parts.hostname, parts.port, authorization)


def _post_refresh(
    url: str,
    body: dict,
    timeout_s: float,
    *,
    egress: Egress | None = None,
    connect=http.client.HTTPSConnection,
) -> tuple[int, dict]:
    """POST the refresh grant exactly as `refresh_request` lays it out, through
    the credential's egress when it has one.

    http.client rather than urllib: urllib re-cases header names and adds its
    own, so the request would stop matching Claude Code's. Returns (status,
    parsed body); raises OSError on transport problems.
    """
    parts = urllib.parse.urlsplit(url)
    headers, raw = refresh_request(body)
    if egress is None:
        conn = connect(parts.hostname, parts.port, timeout=timeout_s)
    else:
        conn = connect(egress.host, egress.port, timeout=timeout_s)
        tunnel_headers = (
            {"Proxy-Authorization": egress.authorization}
            if egress.authorization
            else {}
        )
        conn.set_tunnel(parts.hostname, parts.port or 443, headers=tunnel_headers)
    try:
        conn.putrequest("POST", parts.path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            conn.putheader(name, value)
        conn.endheaders(raw)
        resp = conn.getresponse()
        data = _decoded(resp.read(), resp.getheader("Content-Encoding", ""))
        status = resp.status
    except http.client.HTTPException as err:
        raise OSError(str(err)) from err
    finally:
        conn.close()
    try:
        parsed = json.loads(data or b"{}")
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


class PlatformCredential:
    """The Claude credential in the proxy's credential file, refreshed when it
    is a pair."""

    def __init__(self, path: Path, *, post=_post_refresh, now=time.time) -> None:
        self.path = path
        # The credential's egress, next to it: an `http://[user:pass@]host:port`
        # proxy that every request made with this credential leaves through.
        # Absent or empty, they go direct.
        self.egress_path = path.with_name("egress")
        self._post = post
        self._now = now
        self._lock = threading.Lock()
        self._dead_refresh_tokens: set[str] = set()
        self._last_failure = 0.0

    def _read(self) -> tuple[dict, dict] | str:
        """(document, claudeAiOauth) for a pair, the stripped string otherwise."""
        try:
            text = self.path.read_text().strip()
        except OSError:
            return ""
        if not text.startswith("{"):
            return text
        try:
            doc = json.loads(text)
        except ValueError:
            return ""
        oauth = doc.get("claudeAiOauth") if isinstance(doc, dict) else None
        if not isinstance(oauth, dict) or not oauth.get("accessToken"):
            return ""
        return doc, oauth

    def egress(self) -> Egress | None:
        """Where this credential's requests leave from; None is direct."""
        try:
            return Egress.parse(self.egress_path.read_text())
        except OSError:
            return None

    def token(self) -> tuple[str, str]:
        """(token to put on the request, "") or ("", why there is none)."""
        read = self._read()
        if isinstance(read, str):
            return (read, "") if read else ("", NO_CREDENTIAL)
        _doc, oauth = read
        if oauth.get("refreshToken") in self._dead_refresh_tokens:
            return "", LOGIN_REQUIRED
        expires_at = oauth.get("expiresAt")
        if isinstance(expires_at, int | float) and expires_at / 1000 <= self._now():
            return "", EXPIRED
        return str(oauth["accessToken"]), ""

    def refresh_due(self) -> bool:
        """Cheap check, safe on every request: is a refresh worth attempting?"""
        read = self._read()
        return not isinstance(read, str) and self._due(read[1])

    def _due(self, oauth: dict) -> bool:
        refresh_token = oauth.get("refreshToken")
        if not refresh_token or refresh_token in self._dead_refresh_tokens:
            return False
        if self._now() - self._last_failure < REFRESH_BACKOFF_S:
            return False
        expires_at = oauth.get("expiresAt")
        if not isinstance(expires_at, int | float):
            return True
        return expires_at / 1000 - self._now() <= REFRESH_MARGIN_S

    def refresh_if_due(self) -> None:
        """Refresh the pair once, however many requests found it due at once.

        Blocking; the addon runs it off the event loop.
        """
        with self._lock:
            # Re-read under the lock: whoever held it before may have refreshed
            # already, or someone may have logged in again meanwhile.
            read = self._read()
            if isinstance(read, str) or not self._due(read[1]):
                return
            doc, oauth = read
            refresh_token = str(oauth["refreshToken"])
            body = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
                "scope": " ".join(self._scopes(oauth)),
            }
            try:
                egress = self.egress()
                status, answer = self._post(
                    OAUTH_TOKEN_URL,
                    body,
                    30.0,
                    **({"egress": egress} if egress else {}),
                )
            except OSError as err:
                self._last_failure = self._now()
                _credential_log.warning("claude credential refresh failed: %s", err)
                return
            if status == 200 and answer.get("access_token"):
                self._write(doc, oauth, answer)
                return
            self._last_failure = self._now()
            if status in (400, 401) and answer.get("error") == "invalid_grant":
                self._dead_refresh_tokens.add(refresh_token)
                _credential_log.error(
                    "claude credential refresh token refused (invalid_grant): "
                    "log in again"
                )
                return
            _credential_log.warning("claude credential refresh answered %s", status)

    @staticmethod
    def _scopes(oauth: dict) -> list[str]:
        stored = oauth.get("scopes") if isinstance(oauth.get("scopes"), list) else []
        extra = [
            s for s in stored if s in ("user:projects:read", "user:projects:write")
        ]
        return [*_REFRESH_SCOPES, *extra]

    def _write(self, doc: dict, oauth: dict, answer: dict) -> None:
        now_ms = int(self._now() * 1000)
        refreshed = {
            **oauth,
            "accessToken": answer["access_token"],
            "refreshToken": answer.get("refresh_token") or oauth["refreshToken"],
            "expiresAt": now_ms + int(answer.get("expires_in") or 0) * 1000,
        }
        if isinstance(answer.get("refresh_token_expires_in"), int | float):
            refreshed["refreshTokenExpiresAt"] = (
                now_ms + int(answer["refresh_token_expires_in"]) * 1000
            )
        if isinstance(answer.get("scope"), str) and answer["scope"].strip():
            refreshed["scopes"] = answer["scope"].split()
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        owner = os.stat(self.path)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        # The proxy runs as root in its container; the file stays the host
        # operator's, so the login script can still read and replace it.
        try:
            os.fchown(fd, owner.st_uid, owner.st_gid)
        except PermissionError:
            pass
        with os.fdopen(fd, "w") as fh:
            json.dump({**doc, "claudeAiOauth": refreshed}, fh, separators=(",", ":"))
        os.replace(tmp, self.path)
        login_by = refreshed.get("refreshTokenExpiresAt")
        if isinstance(login_by, int | float):
            left_s = login_by / 1000 - self._now()
            if left_s <= LOGIN_WARNING_S:
                _credential_log.warning(
                    "claude login expires in %.1f days; log in again",
                    left_s / 86400,
                )


# --- the platform's ChatGPT subscription accounts ----------------------------
#
# Held here the way the Claude credential is: this proxy is the only holder of
# each pair and renews it itself. Unlike Claude there can be several, one per
# name, each in `<root>/<name>/credential` with an optional `<name>/egress`
# beside it (the same `http://[user:pass@]host:port` as the Claude one). The
# credential is JSON: access_token, refresh_token, id_token, expires_at (epoch
# seconds) and account_id. chatgpt-login.sh writes it; the proxy re-reads it on
# every request and writes the renewed pair back.
#
# The refresh is OpenAI's token endpoint with the Codex client id, and the
# answers below that mean the pair is dead are OpenAI's. Stdlib only: this file
# ships alone in the proxy image.
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# Refresh this many seconds before the access token expires.
CHATGPT_REFRESH_MARGIN_S = 600
# Refresh answers that mean the pair is dead and someone has to log in again.
# Anything else that is not a 200 (5xx, an unrecognised 4xx, no answer) is
# worth retrying after REFRESH_BACKOFF_S.
_CHATGPT_DEAD_CODES = frozenset(
    {"refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated"}
)
# Where a ChatGPT account's requests go, and the only paths under it that are
# forwarded: the Responses API and the model list. Nothing else a caller names
# is reachable on an account's token — plain segments only, so no `..` or
# encoded one walks out of /codex to the rest of the ChatGPT backend.
CHATGPT_UPSTREAM = ("chatgpt.com", 443)
CHATGPT_BACKEND_PATH = "/backend-api/codex"
_CHATGPT_PATH_RE = re.compile(r"models|responses(/[A-Za-z0-9_-]+)*")
# An account name is one path segment on the route and one directory on disk,
# so nothing that could climb out of either.
_ACCOUNT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# ChatGPT gates which models an account is offered on the Codex client version
# it is told: 0.153.4 is not offered gpt-6-sol or gpt-6-luna, 0.158.0 is. So the
# version is an operator's setting, a file beside the accounts that is read on
# every request (`chatgpt-login.sh client-version`), and this is only what is
# sent while there is none. A plain file among the account directories is not
# an account: an account is a directory.
DEFAULT_CODEX_CLIENT_VERSION = "0.158.0"
CLIENT_VERSION_FILE = "client-version"
# What may go into the header and the query; chatgpt-login.sh refuses the rest
# on the way in, and a file edited by hand into anything else is not sent.
_CLIENT_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}")


def chatgpt_route(path: str) -> tuple[str, str] | None:
    """(account name, upstream path) for `/chatgpt/<name>/<rest>`, else None.

    The upstream path is "" when <rest> is not one this proxy forwards."""
    prefix, sep, rest = path.partition("?")
    parts = prefix.split("/", 3)
    if len(parts) < 4 or parts[0] or parts[1] != "chatgpt" or not parts[2]:
        return None
    name, tail = parts[2], parts[3]
    if not _CHATGPT_PATH_RE.fullmatch(tail):
        return name, ""
    return name, f"{CHATGPT_BACKEND_PATH}/{tail}{sep}{rest}"


def with_client_version(upstream_path: str, version: str) -> str:
    """The model list's path asking for `version`'s models; any other path as is.

    The model list takes the client version as `client_version` in its query
    as well as in the `version` header. The caller's own query value is
    replaced, not kept: one request naming two versions would list one
    version's models while the requests that follow are gated on the other."""
    prefix, _, query = upstream_path.partition("?")
    if prefix != f"{CHATGPT_BACKEND_PATH}/models":
        return upstream_path
    pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True)
        if key != "client_version"
    ]
    pairs.append(("client_version", version))
    return f"{prefix}?{urllib.parse.urlencode(pairs)}"


def jwt_claims(token: str) -> dict:
    """A JWT's payload, unverified: read for metadata (expiry, account id), never
    as proof of anything — the token came straight from OpenAI over TLS."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        claims = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        )
    except (ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def chatgpt_account_id(id_token: str) -> str:
    """The ChatGPT account id an id_token names, at the top level or under the
    `https://api.openai.com/auth` namespace (OpenAI has used both)."""
    claims = jwt_claims(id_token)
    value = claims.get("chatgpt_account_id")
    if not isinstance(value, str) or not value:
        nested = claims.get("https://api.openai.com/auth")
        value = nested.get("chatgpt_account_id") if isinstance(nested, dict) else ""
    return value if isinstance(value, str) else ""


def _post_form(
    url: str,
    fields: dict,
    timeout_s: float,
    *,
    egress: Egress | None = None,
    connect=http.client.HTTPSConnection,
) -> tuple[int, dict]:
    """POST a form to OpenAI's token endpoint, through the account's egress when
    it has one. Returns (status, parsed body); raises OSError on transport
    problems."""
    parts = urllib.parse.urlsplit(url)
    raw = urllib.parse.urlencode(fields).encode()
    if egress is None:
        conn = connect(parts.hostname, parts.port, timeout=timeout_s)
    else:
        conn = connect(egress.host, egress.port, timeout=timeout_s)
        tunnel_headers = (
            {"Proxy-Authorization": egress.authorization}
            if egress.authorization
            else {}
        )
        conn.set_tunnel(parts.hostname, parts.port or 443, headers=tunnel_headers)
    try:
        conn.request(
            "POST",
            parts.path,
            raw,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        resp = conn.getresponse()
        data = resp.read()
        status = resp.status
    except http.client.HTTPException as err:
        raise OSError(str(err)) from err
    finally:
        conn.close()
    try:
        parsed = json.loads(data or b"{}")
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def _refresh_error_code(answer: dict) -> str:
    """The error code of a refused refresh: `error` as an object's `code` or as
    a string, then a top-level `code`."""
    error = answer.get("error")
    code = error.get("code") if isinstance(error, dict) else error
    if not isinstance(code, str):
        code = answer.get("code")
    return code.lower() if isinstance(code, str) else ""


class ChatGPTCredential:
    """One ChatGPT account's pair in `<directory>/credential`, refreshed here."""

    def __init__(self, directory: Path, *, post=_post_form, now=time.time) -> None:
        self.name = directory.name
        self.path = directory / "credential"
        self.egress_path = directory / "egress"
        self._post = post
        self._now = now
        self._lock = threading.Lock()
        self._dead_refresh_tokens: set[str] = set()
        self._last_failure = 0.0

    def _read(self) -> dict | None:
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or not isinstance(doc.get("access_token"), str):
            return None
        return doc if doc["access_token"] else None

    def egress(self) -> Egress | None:
        """Where this account's requests leave from; None is direct."""
        try:
            return Egress.parse(self.egress_path.read_text())
        except OSError:
            return None

    def token(self) -> tuple[str, str, str]:
        """(access token, account id, "") or ("", "", why there is none)."""
        doc = self._read()
        if doc is None:
            return "", "", NO_CREDENTIAL
        if doc.get("refresh_token") in self._dead_refresh_tokens:
            return "", "", LOGIN_REQUIRED
        expires_at = doc.get("expires_at")
        if isinstance(expires_at, int | float) and expires_at <= self._now():
            return "", "", EXPIRED
        account_id = doc.get("account_id")
        return (
            doc["access_token"],
            account_id if isinstance(account_id, str) else "",
            "",
        )

    def refresh_due(self) -> bool:
        """Cheap check, safe on every request: is a refresh worth attempting?"""
        doc = self._read()
        return doc is not None and self._due(doc)

    def _due(self, doc: dict) -> bool:
        refresh_token = doc.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        if refresh_token in self._dead_refresh_tokens:
            return False
        if self._now() - self._last_failure < REFRESH_BACKOFF_S:
            return False
        expires_at = doc.get("expires_at")
        if not isinstance(expires_at, int | float):
            return True
        return expires_at - self._now() <= CHATGPT_REFRESH_MARGIN_S

    def refresh_if_due(self) -> None:
        """Refresh the pair once, however many requests found it due at once.

        Blocking; the addon runs it off the event loop.
        """
        with self._lock:
            doc = self._read()
            if doc is None or not self._due(doc):
                return
            refresh_token = doc["refresh_token"]
            fields = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OPENAI_CLIENT_ID,
                "scope": "openid profile email",
            }
            try:
                egress = self.egress()
                status, answer = self._post(
                    OPENAI_TOKEN_URL,
                    fields,
                    30.0,
                    **({"egress": egress} if egress else {}),
                )
            except OSError as err:
                self._last_failure = self._now()
                _credential_log.warning(
                    "chatgpt account %s refresh failed: %s", self.name, err
                )
                return
            access_token = answer.get("access_token")
            if status == 200 and isinstance(access_token, str) and access_token:
                self._write(doc, answer)
                return
            self._last_failure = self._now()
            if (
                status in (401, 403)
                or _refresh_error_code(answer) in _CHATGPT_DEAD_CODES
            ):
                self._dead_refresh_tokens.add(refresh_token)
                _credential_log.error(
                    "chatgpt account %s refresh token refused (HTTP %s): log in "
                    "again with chatgpt-login.sh login %s",
                    self.name,
                    status,
                    self.name,
                )
                return
            _credential_log.warning(
                "chatgpt account %s refresh answered %s", self.name, status
            )

    def _write(self, doc: dict, answer: dict) -> None:
        access_token = answer["access_token"]
        refreshed = {**doc, "access_token": access_token}
        for key in ("refresh_token", "id_token"):
            if isinstance(answer.get(key), str) and answer[key]:
                refreshed[key] = answer[key]
        account_id = chatgpt_account_id(refreshed.get("id_token") or "")
        if account_id:
            refreshed["account_id"] = account_id
        expires_in = answer.get("expires_in")
        exp = jwt_claims(access_token).get("exp")
        if isinstance(expires_in, int | float) and expires_in > 0:
            refreshed["expires_at"] = int(self._now() + expires_in)
        elif isinstance(exp, int | float):
            refreshed["expires_at"] = int(exp)
        else:
            refreshed["expires_at"] = None
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        owner = os.stat(self.path)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        # The proxy runs as root in its container; the file stays the host
        # operator's, so the login script can still read and replace it.
        try:
            os.fchown(fd, owner.st_uid, owner.st_gid)
        except PermissionError:
            pass
        with os.fdopen(fd, "w") as fh:
            json.dump(refreshed, fh, separators=(",", ":"))
        os.replace(tmp, self.path)


class ChatGPTAccounts:
    """The ChatGPT accounts under one directory, one subdirectory per name.

    An account exists when its directory does; a directory without a usable
    credential is an account that is logged out. Each account's object is kept,
    because its lock and the refresh tokens it has seen refused live on it.
    """

    def __init__(self, root: Path, *, post=_post_form, now=time.time) -> None:
        self.root = root
        self._post = post
        self._now = now
        self._lock = threading.Lock()
        self._accounts: dict[str, ChatGPTCredential] = {}

    def get(self, name: str) -> ChatGPTCredential | None:
        if not _ACCOUNT_NAME_RE.fullmatch(name) or not (self.root / name).is_dir():
            return None
        with self._lock:
            account = self._accounts.get(name)
            if account is None:
                account = ChatGPTCredential(
                    self.root / name, post=self._post, now=self._now
                )
                self._accounts[name] = account
            return account

    def client_version(self) -> str:
        """The Codex client version ChatGPT is told, from the file beside the
        accounts; the built-in default while it is absent or unusable. Read on
        every request, so a change reaches the next one without a restart."""
        try:
            value = (self.root / CLIENT_VERSION_FILE).read_text().strip()
        except OSError:
            return DEFAULT_CODEX_CLIENT_VERSION
        if not _CLIENT_VERSION_RE.fullmatch(value):
            return DEFAULT_CODEX_CLIENT_VERSION
        return value

    def egress_conflict(self, name: str, egress: Egress) -> str:
        """Another account whose egress is the same proxy with other credentials.

        mitmproxy keeps one upstream connection per proxy address, not per
        proxy login, so two such accounts would share whichever tunnel opened
        first — and a proxy that picks its exit by login would put both on one
        exit. The caller refuses instead."""
        try:
            others = sorted(p.name for p in self.root.iterdir() if p.name != name)
        except OSError:
            return ""
        for other in others:
            account = self.get(other)
            theirs = account.egress() if account else None
            if (
                theirs is not None
                and (theirs.host, theirs.port) == (egress.host, egress.port)
                and theirs.authorization != egress.authorization
            ):
                return other
        return ""
