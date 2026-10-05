"""Talking to a seat's model, over the same road a real turn takes.

Four modes, and the point of the distinction is that a probe is only evidence
about a turn when it goes where the turn goes:

  seat     the seat's OWN road, discovered from its environment. A Claude Code
           seat reaches Anthropic through the metering proxy's CONNECT listener
           (``HTTPS_PROXY``, ``provider_env.subscription_provider``); the proxy
           asks ``/llm/admission`` per request and writes the resolved
           ``supply.model`` into the request body. That rewrite is the whole
           point: the model that runs is the *binding's*, not the caller's, so
           a probe on this road exercises exactly what a real turn does. The
           credential is the CONNECT scoped token -- ``HTTPS_PROXY``'s password
           in direct mode, or the tunnel's ``cheese-tunnel.token`` /
           ``CHEESE_CONNECT_TOKEN`` when the transport rides the tunnel helper.
  gateway  ``POST {backend}/llm/v1/messages`` with the seat's own scoped cheese
           token as the Bearer. This is the platform's machine path for
           harnesses that cannot be steered by HTTPS_PROXY (``llm_proxy.py``):
           the backend swaps in the project's virtual gateway key and streams
           the upstream response back. The model is the request body's -- this
           road proves nothing about the *binding*, since its catch-all never
           consults admission, so it is only for sampling the gateway pool.
  proxy    ``HTTPS_PROXY`` given explicitly, token as the proxy password. The
           lower-level form seat mode drives; kept for a hand-set proxy.
  direct   an explicit base URL and key. Only for a trusted endpoint outside
           the platform.

Nothing here reads or prints a credential: the token is taken from the
environment or the tunnel's token file the harness already holds, and is never
echoed.
"""

from __future__ import annotations

import json
import os
import random
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .stats import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_S,
    POST_REASONING_MAX_TOKENS,
    PROBE_MAX_TOKENS,
    PROBE_TEMPERATURE,
)

ANTHROPIC_VERSION = "2023-06-01"

#: Headers worth keeping as evidence. The pool discriminator is deliberate:
#: Anthropic's own listener answers with ``anthropic-ratelimit-*``/``request-id``
#: and LiteLLM's gateway answers with ``x-litellm-*``; a turn that claims the
#: subscription while carrying the gateway's headers is a routing disagreement
#: visible before a single token is read.
EVIDENCE_HEADER_PREFIXES = (
    "x-litellm-",
    "anthropic-ratelimit-",
    "request-id",
    "x-request-id",
    "cf-ray",
)


def evidence_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered.startswith(EVIDENCE_HEADER_PREFIXES):
            out[lowered] = value
    return out


def pool_from_headers(headers: dict[str, str]) -> str | None:
    """Which pool answered, read off the response shell rather than the claim."""
    if any(k.startswith("x-litellm-") for k in headers):
        return "gateway"
    if (
        any(k.startswith("anthropic-ratelimit-") for k in headers)
        or "request-id" in headers
    ):
        return "subscription"
    return None


@dataclass
class Completion:
    text: str
    model_echo: str | None
    usage: dict
    headers: dict[str, str]
    latency_ms: float
    error: str | None = None


@dataclass
class Endpoint:
    mode: str  # seat | gateway | proxy | direct
    model: str
    backend: str = ""
    connector_token_env: str = "CHEESE_TOKEN"
    base_url: str = ""
    api_key_env: str = ""
    ca_path: str = ""
    proxy_url: str = ""
    protocol: str = "anthropic-messages"  # or openai-chat
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: Where the seat's CONNECT credential came from, and the address the
    #: listener is at when the token came from a file rather than the proxy URL.
    credential_source: str = ""
    connect_host: str = ""

    # ---- construction helpers ------------------------------------------------
    @classmethod
    def gateway(cls, model: str, backend: str | None = None) -> Endpoint:
        # CHEESE_API is the address the platform injects for the harness's own
        # calls; CHEESE_BACKEND is not a variable the platform ever sets, and
        # reading it made every default run dial a box-local address that only
        # exists on the backend's host.
        return cls(
            mode="gateway",
            model=model,
            backend=backend or os.environ.get("CHEESE_API") or "http://172.17.0.1:8081",
        )

    @classmethod
    def proxy(
        cls, model: str, proxy_url: str | None = None, ca_path: str | None = None
    ) -> Endpoint:
        return cls(
            mode="proxy",
            model=model,
            proxy_url=proxy_url or os.environ.get("HTTPS_PROXY", ""),
            ca_path=ca_path or os.environ.get("CHEESE_PROXY_CA", ""),
        )

    @classmethod
    def seat(
        cls,
        model: str,
        *,
        proxy_url: str | None = None,
        connect_host: str | None = None,
        ca_path: str | None = None,
    ) -> Endpoint:
        """The seat's own road, discovered from its environment.

        Credential, in the order a real turn would have it:

          1. the password embedded in ``HTTPS_PROXY`` (the direct listener);
          2. ``CHEESE_CONNECT_TOKEN`` (what the launch script writes for the
             tunnel helper);
          3. ``$HOME/.cheese/cheese-tunnel.token`` (where that helper reads it).

        A URL is built from the credential and the listener address only when
        the proxy URL itself carries none -- the tunnel's ``HTTPS_PROXY`` is the
        helper's loopback port with no userinfo, and only the helper knows the
        token, but its token file is right here. Nothing is printed.
        """
        resolved = _discover_connect(proxy_url, connect_host)
        return cls(
            mode="seat",
            model=model,
            proxy_url=resolved.url,
            connect_host=resolved.host,
            ca_path=ca_path or _discover_ca(),
            credential_source=resolved.source,
        )

    @classmethod
    def direct(cls, model: str, base_url: str, api_key_env: str) -> Endpoint:
        return cls(
            mode="direct", model=model, base_url=base_url, api_key_env=api_key_env
        )

    # ---- requests ------------------------------------------------------------
    def _client(self, *, stream: bool = False) -> httpx.Client:
        if self.mode in ("proxy", "seat"):
            return httpx.Client(
                proxy=self.proxy_url,
                verify=self.ca_path or True,
                timeout=DEFAULT_TIMEOUT_S,
                trust_env=False,
            )
        return httpx.Client(timeout=DEFAULT_TIMEOUT_S, trust_env=False)

    def _url(self) -> str:
        if self.mode == "gateway":
            path = (
                "/chat/completions" if self.protocol == "openai-chat" else "/messages"
            )
            return f"{self.backend.rstrip('/')}/llm/v1{path}"
        if self.mode == "direct":
            path = (
                "/chat/completions"
                if self.protocol == "openai-chat"
                else "/v1/messages"
            )
            return f"{self.base_url.rstrip('/')}{path}"
        # proxy: the CONNECT road always speaks the Anthropic wire protocol.
        return "https://api.anthropic.com/v1/messages"

    def _headers(self) -> dict[str, str]:
        headers = {
            "anthropic-version": ANTHROPIC_VERSION,
            "accept": "text/event-stream",
        }
        if self.protocol == "openai-chat":
            headers = {"content-type": "application/json"}
        if self.mode in ("gateway", "proxy", "seat"):
            token = os.environ.get(self.connector_token_env, "")
            headers["authorization"] = f"Bearer {token}"
        elif self.mode == "direct":
            headers["x-api-key"] = os.environ.get(self.api_key_env, "")
            headers["authorization"] = f"Bearer {os.environ.get(self.api_key_env, '')}"
        headers.update(self.extra_headers)
        return headers

    def complete(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = PROBE_MAX_TOKENS,
        temperature: float | None = PROBE_TEMPERATURE,
        extra_body: dict | None = None,
        stream: bool = True,
    ) -> Completion:
        """One probe. Streams by default: some gateway routes refuse a
        non-streamed request outright, and streaming is what a real turn does.

        ``temperature`` is sent *explicitly* (the paper protocol's 1.0, not
        omitted): a route that silently defaults a missing temperature to
        something else would change the distribution under test. ``None`` omits
        the field, which a few strict OpenAI-proxied routes require -- that
        deviation is discovered by ``detect_adapter`` and recorded in the
        reference's notes, never applied silently. ``extra_body`` carries the
        reasoning-disable field (see ``detect_adapter``); without it a reasoning
        model spends ``max_tokens`` on hidden thinking and every sample
        normalises to empty.
        """
        if self.protocol == "openai-chat":
            body: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }
        else:
            body = {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
        if temperature is not None:
            body["temperature"] = temperature
        if extra_body:
            body.update(extra_body)
        if stream:
            body["stream"] = True

        last_error: str | None = None
        for attempt in range(DEFAULT_MAX_RETRIES + 1):
            started = time.monotonic()
            try:
                with self._client(stream=stream) as client:
                    if stream:
                        result = self._stream(client, body)
                    else:
                        result = self._once(client, body)
                if result.error is None:
                    result.latency_ms = (time.monotonic() - started) * 1000
                    return result
                last_error = result.error
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            except ValueError as exc:
                # httpx raises ValueError for a malformed proxy URL and puts the
                # whole URL -- credential included -- in the message. Keep the
                # type only; retrying a malformed URL cannot help.
                last_error = f"{type(exc).__name__}: the proxy URL is malformed"
                break
            time.sleep(0.4 * (attempt + 1))
        return Completion(
            text="",
            model_echo=None,
            usage={},
            headers={},
            latency_ms=0.0,
            error=last_error or "unknown transport error",
        )

    def _stream(self, client: httpx.Client, body: dict) -> Completion:
        headers = self._headers()
        with client.stream("POST", self._url(), json=body, headers=headers) as response:
            if response.status_code != 200:
                detail = response.read().decode(errors="replace")[:400]
                return Completion(
                    "", None, {}, {}, 0.0, f"HTTP {response.status_code}: {detail}"
                )
            evidence = evidence_headers(response.headers)
            text_parts: list[str] = []
            model_echo: str | None = None
            usage: dict = {}
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if self.protocol == "openai-chat":
                    text_parts, model_echo, usage = _absorb_openai(
                        event, text_parts, model_echo, usage
                    )
                else:
                    text_parts, model_echo, usage = _absorb(
                        event, text_parts, model_echo, usage
                    )
        return Completion("".join(text_parts), model_echo, usage, evidence, 0.0)

    def _once(self, client: httpx.Client, body: dict) -> Completion:
        response = client.post(self._url(), json=body, headers=self._headers())
        if response.status_code != 200:
            return Completion(
                "",
                None,
                {},
                {},
                0.0,
                f"HTTP {response.status_code}: {response.text[:400]}",
            )
        data = response.json()
        evidence = evidence_headers(response.headers)
        if self.protocol == "openai-chat":
            choice = (data.get("choices") or [{}])[0]
            return Completion(
                choice.get("message", {}).get("content", "") or "",
                data.get("model"),
                data.get("usage") or {},
                evidence,
                0.0,
            )
        text = "".join(
            b.get("text", "")
            for b in data.get("content", [])
            if b.get("type") == "text"
        )
        return Completion(
            text, data.get("model"), data.get("usage") or {}, evidence, 0.0
        )


def _absorb(
    event: dict, text_parts: list[str], model_echo: str | None, usage: dict
) -> tuple[list[str], str | None, dict]:
    kind = event.get("type")
    if kind == "message_start":
        message = event.get("message") or {}
        model_echo = message.get("model") or model_echo
        if message.get("usage"):
            usage = {**(usage or {}), **message["usage"]}
    elif kind == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            text_parts.append(delta["text"])
    elif kind == "message_delta":
        if event.get("usage"):
            usage = {**(usage or {}), **event["usage"]}
    elif kind == "message_stop" and event.get("usage"):
        usage = {**(usage or {}), **event["usage"]}
    return text_parts, model_echo, usage


def _absorb_openai(
    event: dict, text_parts: list[str], model_echo: str | None, usage: dict
) -> tuple[list[str], str | None, dict]:
    """One OpenAI-compatible SSE chunk: ``choices[].delta.content`` for the
    text, a top-level ``model``, and the ``usage`` block that (with
    ``stream_options.include_usage``) arrives on the final chunk."""
    if event.get("model"):
        model_echo = event["model"]
    for choice in event.get("choices") or []:
        delta = choice.get("delta") or {}
        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            text_parts.append(piece)
    if event.get("usage"):
        usage = {**(usage or {}), **event["usage"]}
    return text_parts, model_echo, usage


def proxy_ca_candidates() -> list[str]:
    """Where a metering proxy's CA is likely to be on a box that runs one."""
    return [
        os.environ.get("CHEESE_PROXY_CA", ""),
        os.environ.get("NODE_EXTRA_CA_CERTS", ""),
        os.path.join(os.path.expanduser("~"), ".claude", "proxy-ca.pem"),
    ]


def _discover_ca() -> str:
    for candidate in proxy_ca_candidates():
        if candidate and Path(candidate).exists():
            return candidate
    return ""


@dataclass(frozen=True)
class ConnectTarget:
    """Where a seat's CONNECT credential lives and what the listener is at."""

    url: str
    host: str
    source: str


def _discover_connect(proxy_url: str | None, connect_host: str | None) -> ConnectTarget:
    """Find the seat's own CONNECT road without ever printing the credential.

    Order follows what a real turn has: the proxy URL (direct listener, token
    as its password) first; then the tunnel's token file / ``CHEESE_CONNECT_TOKEN``
    (tunnel mode, URL is the helper's loopback port with no userinfo). A token
    found in a file is folded back into a proxy URL here -- it is used only to
    authenticate the probe's own CONNECT, which is the one thing a proxy
    credential is for.
    """
    given = (proxy_url or os.environ.get("HTTPS_PROXY", "")).strip()
    if given:
        return ConnectTarget(url=given, host="", source="HTTPS_PROXY")
    token = os.environ.get("CHEESE_CONNECT_TOKEN", "").strip()
    source = "CHEESE_CONNECT_TOKEN"
    if not token:
        token_file = Path(
            os.environ.get(
                "CHEESE_CONNECT_TOKEN_FILE",
                os.path.join(os.path.expanduser("~"), ".cheese", "cheese-tunnel.token"),
            )
        )
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip()
            source = str(token_file)
    host = (
        connect_host or os.environ.get("CHEESE_CONNECT_HOST", "") or "172.17.0.1:8444"
    )
    if not token:
        # No credential: still return the address, so the failure names the
        # missing piece rather than silently dialling nothing.
        return ConnectTarget(url=f"http://{host}", host=host, source="none")
    return ConnectTarget(url=f"http://cheese:{token}@{host}", host=host, source=source)


# --- reasoning disable (adapter) --------------------------------------------
#
# Hidden thinking must be off: it burns the max_tokens budget before any visible
# answer appears and shifts the sampled distribution. Ported from the upstream
# ``adapter.ts``; the field that works differs by protocol, and the gateway's
# Anthropic-shaped route takes ``thinking: {type: disabled}`` while the
# OpenAI-shaped one takes ``reasoning_effort: none``. Each is probed once; the
# first that returns a non-empty visible answer wins. If none works we raise the
# budget to ``POST_REASONING_MAX_TOKENS`` and flag the run as lower confidence.

REASONING_DISABLE_BODIES: dict[str, list[tuple[str, dict]]] = {
    "anthropic-messages": [("anthropic-thinking", {"thinking": {"type": "disabled"}})],
    "openai-chat": [
        ("openai-effort", {"reasoning_effort": "none"}),
        ("zhipu-thinking", {"thinking": {"type": "disabled"}}),
        ("openrouter-reasoning", {"reasoning": {"enabled": False}}),
    ],
}


@dataclass
class ReasoningAdapter:
    strategy: str
    extra_body: dict = field(default_factory=dict)
    max_tokens: int = PROBE_MAX_TOKENS
    post_reasoning: bool = False
    #: A few strict OpenAI-proxied gateway routes reject the paper's explicit
    #: ``temperature`` with ``Unsupported parameter: temperature``. Discovered
    #: once and recorded, so the reference's notes say which protocol it was
    #: drawn under; the default stays "send 1.0".
    omit_temperature: bool = False

    @property
    def temperature(self) -> float | None:
        return None if self.omit_temperature else PROBE_TEMPERATURE


#: How an upstream says it will not take the explicit temperature. Matched
#: case-insensitively against the error body; kept narrow so a generic 400 is
#: not mistaken for this.
_TEMPERATURE_REJECTED = "unsupported parameter: temperature"


def detect_adapter(
    endpoint: Endpoint, cell_id: str = "random-number-1-100:en"
) -> ReasoningAdapter:
    """Find the reasoning-disable field the endpoint accepts, and whether it
    takes the paper's explicit temperature. One probe each, bare request as
    fallback, post-reasoning budget as the last resort."""
    from . import battery  # local import: battery imports stats, not transport

    system = battery.system_prompt(cell_id)
    prompt = battery.pick_paraphrase(cell_id, random.Random(0))

    # Send the paper temperature first; if the upstream names it as unsupported,
    # drop it for every subsequent probe and flag the reference as drawn without
    # it. No silent fallback: the flag rides into the notes.
    first = endpoint.complete(system, prompt, temperature=PROBE_TEMPERATURE)
    omit_temperature = bool(
        first.error and _TEMPERATURE_REJECTED in first.error.lower()
    )

    def probe(extra_body: dict | None = None) -> Completion:
        return endpoint.complete(
            system,
            prompt,
            temperature=None if omit_temperature else PROBE_TEMPERATURE,
            extra_body=extra_body,
        )

    for strategy, body in REASONING_DISABLE_BODIES.get(endpoint.protocol, []):
        completion = probe(body)
        if completion.error is None and completion.text.strip():
            return ReasoningAdapter(
                strategy, body, PROBE_MAX_TOKENS, False, omit_temperature
            )
    bare = probe()
    if bare.error is None and bare.text.strip():
        return ReasoningAdapter("none", {}, PROBE_MAX_TOKENS, False, omit_temperature)
    return ReasoningAdapter(
        "none",
        {},
        POST_REASONING_MAX_TOKENS,
        post_reasoning=True,
        omit_temperature=omit_temperature,
    )


def ssl_context(ca_path: str) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_path)
