"""One chat completion on the platform's gateway, for features that are not an
agent's turn in a room: turning a PDF into task drafts, sorting old memories.

The caller brings the virtual key (``service_keys.service_key``), which decides
whose money it is: a key without a budget for work a person asked for and pays
for from their personal credits, a key with a budget for work the platform does
on its own. What comes back carries the usage the gateway reported, cache
shares included, so the caller can charge it at the model's price.
"""

import base64
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from app.core.sentences import say
from app.domain.service_keys import gateway_base

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Usage:
    """Tokens one or more calls spent. ``prompt_tokens`` counts every prompt
    token; ``cache_read_tokens`` and ``cache_write_tokens`` are the shares of it
    read from and written to the provider's cache."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    @classmethod
    def of(cls, usage: object) -> "Usage":
        """Read an OpenAI-shaped ``usage`` object as the gateway returns it."""
        if not isinstance(usage, dict):
            return cls()
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if cached is None:
            cached = usage.get("prompt_cache_hit_tokens")
        return cls(
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(cached or 0),
            int(usage.get("cache_creation_input_tokens") or 0),
        )


@dataclass(frozen=True)
class Completion:
    content: str
    usage: Usage
    #: The gateway's price for the call; 0.0 when it named none.
    cost_usd: float = 0.0


def response_cost(response: httpx.Response) -> float:
    """The price the gateway put on a call (``x-litellm-response-cost``); 0.0,
    read as unpriced, when it put none."""
    try:
        cost = float(response.headers.get("x-litellm-response-cost") or 0.0)
    except ValueError:
        return 0.0
    return cost if math.isfinite(cost) and cost > 0 else 0.0


class GatewayCallError(Exception):
    """The gateway did not answer, or answered with an error."""


class GatewayChat:
    def __init__(
        self,
        key: str,
        model: str,
        *,
        max_tokens: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = key
        self._model = model
        self._max_tokens = max_tokens
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        timeout: float,
        json_response: bool = False,
        images: Sequence[bytes] | None = None,
    ) -> Completion:
        body: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._user_content(prompt, images)},
            ],
            "max_tokens": self._max_tokens,
        }
        if json_response:
            body["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout), transport=self._transport
            ) as client:
                r = await client.post(
                    f"{gateway_base()}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise GatewayCallError(
                say("gatewayModelTimeout", seconds=format(timeout, ".0f"))
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayCallError(say("modelGatewayUnreachable")) from exc
        if r.status_code != 200:
            logger.warning("gateway answered %s: %s", r.status_code, r.text[:300])
            raise GatewayCallError(say("gatewayReturnedStatus", status=r.status_code))
        payload = r.json()
        choices = payload.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content") or ""
        return Completion(content, Usage.of(payload.get("usage")), response_cost(r))

    @staticmethod
    def _user_content(prompt: str, images: Sequence[bytes] | None) -> str | list[dict]:
        """The user turn: plain text, or text plus inline PNGs when there are any.

        The gateway speaks the OpenAI chat-completions shape, where an image rides
        in a content list beside the text as a ``data:`` URL. A caller that passes
        no images gets the plain string it always did, so nothing about the
        text-only path changes.
        """
        if not images:
            return prompt
        blocks: list[dict] = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )
        return blocks
