"""Talking to a seat's model, over the same road a real turn takes.

Three modes, and the point of the distinction is that a probe is only evidence
about a turn when it goes where the turn goes:

  gateway  ``POST {backend}/llm/v1/messages`` with the seat's own scoped cheese
           token as the Bearer. This is the platform's machine path for
           harnesses that cannot be steered by HTTPS_PROXY (``llm_proxy.py``):
           the backend swaps in the project's virtual gateway key and streams
           the upstream response back. The model is the request body's, so any
           gateway-pool model can be sampled.
  proxy    ``HTTPS_PROXY`` pointing at the metering proxy's CONNECT listener,
           with the seat's scoped token as the proxy password -- Claude Code's
           own road (``provider_env.subscription_provider``). The proxy rewrites
           the model in the body from the admission verdict, so the model here
           is the one the *binding* names, not the caller.
  direct   an explicit base URL and key. Only for a trusted endpoint outside
           the platform.

Nothing here reads or prints a credential: the token is taken from the
environment variable the harness already holds and is never echoed.
"""

from __future__ import annotations

import json
import os
import ssl
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .stats import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_S,
    PROBE_MAX_TOKENS,
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
    mode: str  # gateway | proxy | direct
    model: str
    backend: str = ""
    connector_token_env: str = "CHEESE_TOKEN"
    base_url: str = ""
    api_key_env: str = ""
    ca_path: str = ""
    proxy_url: str = ""
    protocol: str = "anthropic-messages"  # or openai-chat
    extra_headers: dict[str, str] = field(default_factory=dict)

    # ---- construction helpers ------------------------------------------------
    @classmethod
    def gateway(cls, model: str, backend: str | None = None) -> Endpoint:
        return cls(
            mode="gateway",
            model=model,
            backend=backend
            or os.environ.get("CHEESE_BACKEND", "http://172.17.0.1:8081"),
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
    def direct(cls, model: str, base_url: str, api_key_env: str) -> Endpoint:
        return cls(
            mode="direct", model=model, base_url=base_url, api_key_env=api_key_env
        )

    # ---- requests ------------------------------------------------------------
    def _client(self) -> httpx.Client:
        if self.mode == "proxy":
            return httpx.Client(
                proxy=self.proxy_url,
                verify=self.ca_path or True,
                timeout=DEFAULT_TIMEOUT_S,
                trust_env=False,
            )
        if self.mode == "direct":
            return httpx.Client(timeout=DEFAULT_TIMEOUT_S, trust_env=False)
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
        if self.mode in ("gateway", "proxy"):
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
        temperature: float | None = None,
        stream: bool = True,
    ) -> Completion:
        """One probe. Streams by default: some gateway routes refuse a
        non-streamed request outright, and streaming is what a real turn does."""
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
            # Omitted rather than sent as 1.0: some routes reject the parameter
            # outright, and 1.0 is every provider's own default anyway.
            body["temperature"] = temperature
        if stream:
            body["stream"] = True

        last_error: str | None = None
        for attempt in range(DEFAULT_MAX_RETRIES + 1):
            started = time.monotonic()
            try:
                with self._client() as client:
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


def proxy_ca_candidates() -> list[str]:
    """Where a metering proxy's CA is likely to be on a box that runs one."""
    return [
        os.environ.get("CHEESE_PROXY_CA", ""),
        "/home/nictheboy/cheese-proxy/certs/mitmproxy-ca-cert.pem",
    ]


def ssl_context(ca_path: str) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_path)
