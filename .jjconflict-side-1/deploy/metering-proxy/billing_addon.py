"""mitmproxy addon: inject the real credential, meter every Claude turn, cap it.

This sits between a sandbox's Claude Code and the upstream, and it is the ONE
place the real credential lives. The sandbox MUST NOT hold a valid credential
(hard requirement: a machine that leaks an auth key is a machine that leaks the
subscription). So the container ships a scoped cheese token — enough to make
Claude Code believe it is logged in and to authenticate "bill this project" —
and this proxy rewrites the Authorization header to the real token on the way
out. The real token never touches the container's disk or environment.

That placement also makes this the only point that can:
  - meter a subscription turn's real cost (the subscription path deliberately
    skips LiteLLM — no per-call key to meter, and re-originating from our own
    client would change what the provider sees),
  - enforce a cap BEFORE forwarding, so an exhausted budget cannot overspend:
    per-project via the backend's /llm/admission (#218), plus the rolling
    token window as the deployment-wide backstop,
  - refresh the real token in ONE place (a single-flight loop writes the token
    file this reads), so no two sandboxes race a rotation and kill it.

Attribution comes from the VERIFIED claims of the caller's scoped token (#198)
when CHEESE_SCOPED_SECRET is set; the legacy x-cheese-attr header is honored
only when CHEESE_ALLOW_HEADER_ATTR=1 (bridge-only deployments still on the
fixed placeholder token). On a proxy exposed beyond the box's own docker
bridge, leave that off — the header is whatever the machine says it is.

  mitmdump -s billing_addon.py --mode reverse:https://api.anthropic.com

Config (env): CHEESE_USAGE_LOG, CHEESE_INJECT_TOKEN, CHEESE_TOKEN_CAP,
CHEESE_CAP_WINDOW_S, CHEESE_UPSTREAM_VIA, CHEESE_SCOPED_SECRET,
CHEESE_ALLOW_HEADER_ATTR, CHEESE_ADMISSION_URL, CHEESE_ADMISSION_CACHE_S.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from mitmproxy import http

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cheese_billing_core import (  # noqa: E402
    AdmissionGate,
    Meter,
    usage_from_sse,
    verify_scoped_token,
)

logger = logging.getLogger("cheese.metering")

X_ATTR_HEADER = "x-cheese-attr"  # legacy "<project_id>/<topic_id>", spoofable
USAGE_LOG = Path(os.environ.get("CHEESE_USAGE_LOG", "/var/log/cheese/usage.jsonl"))
# 0 disables the backstop cap. Set per deployment from the subscription's ceiling.
TOKEN_CAP = int(os.environ.get("CHEESE_TOKEN_CAP", "0"))
CAP_WINDOW_S = int(os.environ.get("CHEESE_CAP_WINDOW_S", str(5 * 3600)))
# File holding the REAL OAuth access token (just the token string, no JSON). A
# single-flight refresh loop owns this file; the addon only reads it, re-reading
# each request so a rotation is picked up without a proxy restart. Absent/empty =
# inject nothing (the sandbox's token goes through and Anthropic 401s — the
# honest failure when we have no credential, never a silent one).
INJECT_TOKEN_FILE = Path(
    os.environ.get("CHEESE_INJECT_TOKEN", "/etc/cheese/inject.token")
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

METER = Meter(USAGE_LOG, CAP_WINDOW_S)
ADMISSION = AdmissionGate(ADMISSION_URL, cache_s=ADMISSION_CACHE_S)


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


def _attribution(flow: http.HTTPFlow) -> tuple[str, str, str]:
    """(project_id, topic_id, caller_bearer) for this request.

    Verified claims first; the spoofable header only where explicitly allowed.
    Empty project = unattributable (recorded as such; refused separately when
    scoped auth is required)."""
    bearer = _caller_bearer(flow)
    if SCOPED_SECRET:
        claims = verify_scoped_token(bearer, SCOPED_SECRET)
        if claims:
            return str(claims.get("p") or ""), str(claims.get("t") or ""), bearer
    if ALLOW_HEADER_ATTR:
        attr = flow.request.headers.get(X_ATTR_HEADER, "")
        project, _, topic = attr.partition("/")
        return project, topic, bearer
    return "", "", bearer


def _refuse(flow: http.HTTPFlow, status: int, kind: str, message: str) -> None:
    flow.response = http.Response.make(
        status,
        json.dumps(
            {"type": "error", "error": {"type": kind, "message": message}}
        ).encode(),
        {"Content-Type": "application/json"},
    )


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

    via = _via()
    if via is not None:
        flow.server_conn.via = via

    # Attribution BEFORE the swap: the caller's own Bearer is the scoped token.
    project_id, topic_id, bearer = _attribution(flow)
    flow.metadata["cheese_attr"] = (project_id, topic_id)

    token = _real_token()
    is_messages = "/v1/messages" in flow.request.path

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
        if project_id and ADMISSION_URL:
            # Off-loop: urllib blocks, and one slow admission call must not
            # stall every other flow through the proxy.
            allow, reason = await asyncio.to_thread(ADMISSION.check, project_id, bearer)
            if not allow:
                _refuse(
                    flow,
                    429,
                    "rate_limit_error",
                    f"cheese project budget: {reason}",
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

    # Inject the real credential on EVERY request, not just messages: Claude
    # Code validates its login against api/oauth/profile at startup, so if only
    # /v1/messages carried the real token that check would 401 and the turn
    # would never start.
    if token:
        flow.request.headers["authorization"] = f"Bearer {token}"
        # A stale x-api-key would override the bearer on Anthropic's side.
        flow.request.headers.pop("x-api-key", None)


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
