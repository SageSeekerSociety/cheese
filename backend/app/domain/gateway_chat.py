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
from collections.abc import Mapping, Sequence
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
    #: The provider's ``finish_reason`` for the first choice (``"stop"``,
    #: ``"length"`` …); empty when the gateway named none. ``"length"`` means
    #: the answer was cut at ``max_tokens`` and a JSON one is very likely
    #: unparseable (see ``looks_cut_short``).
    finish_reason: str = ""

    @property
    def cut_at_length(self) -> bool:
        """The answer stopped because it ran into ``max_tokens``, not because
        the model was done."""
        return self.finish_reason == "length"


def response_cost(response: httpx.Response) -> float:
    """The price the gateway put on a call (``x-litellm-response-cost``); 0.0,
    read as unpriced, when it put none."""
    try:
        cost = float(response.headers.get("x-litellm-response-cost") or 0.0)
    except ValueError:
        return 0.0
    return cost if math.isfinite(cost) and cost > 0 else 0.0


def looks_cut_short(content: str) -> bool:
    """The answer looks like a JSON object cut off before it closed.

    A ``{`` was opened and never matched, or the text ends inside a string
    (odd number of ``"``): the shape a call stopped at ``max_tokens`` leaves.
    A genuinely malformed but complete answer does not look like this — it
    closes its braces — so this separates "ran out of room" from "the model
    wrote nonsense", which are different problems worth different logs.
    """
    body = content.strip()
    if not body:
        return False
    if body.count("{") > body.count("}"):
        return True
    return body.count('"') % 2 == 1


def unreadable_note(completion: Completion) -> str:
    """Why an answer could not be read as JSON: cut off, or malformed.

    ``finish_reason`` is the provider saying outright it hit ``max_tokens``;
    failing that, a brace left open or a string left unterminated is the shape
    a cut-off JSON leaves. Callers put this in the log so a truncated answer
    reads as ``cut at max_tokens`` and not as a vague "unreadable", which is
    what made this bug hard to see the first time.
    """
    if completion.cut_at_length:
        return "cut at max_tokens"
    if looks_cut_short(completion.content):
        return "looks cut off mid-JSON"
    return "malformed"


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
        max_tokens: int | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> Completion:
        """``max_tokens`` overrides the client's for this call; ``extra`` adds
        provider fields to the request (``thinking`` to switch reasoning off)."""
        body: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._user_content(prompt, images)},
            ],
            "max_tokens": max_tokens or self._max_tokens,
            **(extra or {}),
        }
        if json_response:
            body["response_format"] = {"type": "json_object"}
        return await self._post(body, timeout)

    async def transcribe(
        self,
        *,
        audio: bytes,
        media_type: str,
        context: str,
        vocabulary: Mapping[str, int],
        timeout: float,
    ) -> Completion:
        """What is said in ``audio``, from a speech model behind the gateway.

        The gateway serves speech models on the chat route (its ``qwen_asr``
        handler): the audio rides in the last user message as a ``data:`` URL,
        the system message is context the recogniser reads first, and
        ``vocabulary`` maps words it should favour to a weight of 1-5."""
        encoded = base64.b64encode(audio).decode("ascii")
        body: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": context},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:{media_type};base64,{encoded}"
                            },
                        }
                    ],
                },
            ],
        }
        if vocabulary:
            body["vocabulary"] = dict(vocabulary)
        return await self._post(body, timeout)

    async def _post(self, body: dict, timeout: float) -> Completion:
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
        first = choices[0]
        content = (first.get("message") or {}).get("content") or ""
        return Completion(
            content,
            Usage.of(payload.get("usage")),
            response_cost(r),
            str(first.get("finish_reason") or ""),
        )

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
