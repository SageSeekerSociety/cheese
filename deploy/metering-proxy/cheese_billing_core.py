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


@dataclass(frozen=True)
class Verdict:
    """The backend's answer for one project: may it run, and where does it go.

    ``pool`` defaults to the subscription because that is the only destination
    a proxy older than the supply decision ever had — an admission response
    without a ``supply`` block must keep behaving exactly as before.
    """

    allow: bool
    reason: str
    pool: str = SUBSCRIPTION
    key: str | None = None
    # `user:password` — which ccproxy identity to authenticate the upstream hop
    # as for this project's turns. Present only when the backend knows the
    # machine those turns run on; ccproxy scopes its fake→real ticket swap to
    # the authenticated connection, so this is what lets the machine's own
    # ticket be forwarded untouched instead of swapped for one the proxy holds.
    # None = fall back to the deployment-wide identity, and to the swap.
    upstream: str | None = None


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
    return Verdict(
        allow=bool(data.get("allow", True)),
        reason=str(data.get("reason", "")),
        pool=pool if pool in (SUBSCRIPTION, GATEWAY) else SUBSCRIPTION,
        key=supply.get("key") or None,
        # Shape-checked here rather than at use: a half credential ("m516:" or
        # ":pw") would authenticate as nobody, and failing at the parse names
        # the control plane as the source instead of surfacing as an upstream
        # 407 several hops away.
        upstream=upstream
        if isinstance(upstream, str) and len(upstream.split(":", 1)) == 2
        and all(upstream.split(":", 1))
        else None,
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
        self._cache: dict[tuple[str, str], tuple[float, Verdict]] = {}

    def check(self, project_id: str, topic_id: str, bearer: str) -> Verdict:
        if not self._url or not project_id:
            return Verdict(True, "admission not configured")
        key = (project_id, topic_id)
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
