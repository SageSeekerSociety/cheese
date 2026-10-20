"""Give DeepSeek back the thinking its thinking mode requires.

DeepSeek's Anthropic-compatible Messages API runs thinking on by default and
refuses a request when an assistant message no longer carries the thinking
block it answered with:

    The `content[].thinking` in the thinking mode must be passed back to the API.

Claude Code resends the whole conversation on every turn, and some of its own
paths drop those blocks (compacting, clearing old thinking, resuming). A
conversation that loses one therefore fails every later request, retries
included — the same shape as a broken image before `image_guard`.

The repair hangs off the DeepSeek provider's own Messages transformation, not
off a model name: a model group may be called anything and still route here
(an alias such as `cheesex-opus-cc` is in the deployment's history). Every
assistant message without a non-empty `thinking` block gets one. The text is a
placeholder and the signature empty: DeepSeek accepts both and does not verify
the signature. A message that already carries its thinking is passed untouched,
so nothing is added to the token cost of a healthy turn. When the caller asked
for thinking off, nothing is added — DeepSeek refuses a thinking block in a
request that asked for none.

Installed into site-packages as `cheese_deepseek_thinking` and called from the
worker startup hooks (`LITELLM_WORKER_STARTUP_HOOKS` in the Dockerfile), which
is when LiteLLM has loaded its provider classes and before the proxy serves a
request.
"""

import logging

logger = logging.getLogger("cheese.deepseek_thinking")
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(logging.StreamHandler())

#: What goes in the thinking block when the real one is gone. A non-empty
#: string is what DeepSeek checks for; the words themselves carry no meaning.
PLACEHOLDER = "(thinking omitted)"
#: Block types that count as thinking for the check.
THINKING = frozenset({"thinking", "redacted_thinking"})


def _blocks(message: dict) -> list | None:
    content = message.get("content")
    return content if isinstance(content, list) else None


def _fill(blocks: list) -> list:
    """The blocks with a thinking block added, or filled when it is empty.

    Returns the same list when there is nothing to repair, so an untouched
    message stays the object it was.
    """
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") not in THINKING:
            continue
        if block.get("type") == "thinking" and not block.get("thinking"):
            return [
                *blocks[:index],
                {**block, "thinking": PLACEHOLDER},
                *blocks[index + 1 :],
            ]
        return blocks

    return [
        {"type": "thinking", "thinking": PLACEHOLDER, "signature": ""},
        *blocks,
    ]


def repaired(messages: list) -> tuple[list, int]:
    """The messages with every assistant message's thinking filled in, and
    how many messages changed."""
    filled = 0
    result = []
    for message in messages:
        if not isinstance(message, dict):
            result.append(message)
            continue
        blocks = _blocks(message)
        if message.get("role") == "assistant" and blocks is not None:
            new_blocks = _fill(blocks)
            if new_blocks is not blocks:
                message = {**message, "content": new_blocks}
                filled += 1
        result.append(message)
    return result, filled


def _thinking_disabled(request: dict) -> bool:
    thinking = request.get("thinking")
    return isinstance(thinking, dict) and thinking.get("type") == "disabled"


def repair_request(request: dict, model: str) -> dict:
    """The request with every assistant message's thinking filled in."""
    if _thinking_disabled(request):
        return request
    messages = request.get("messages")
    if not isinstance(messages, list):
        return request
    messages, filled = repaired(messages)
    if not filled:
        return request
    logger.info(
        "deepseek_thinking filled a missing thinking block: messages=%d model=%s",
        filled,
        model,
    )
    return {**request, "messages": messages}


def install() -> None:
    """Wrap DeepSeek's Messages transformation so every request is repaired."""
    from litellm.llms.deepseek.messages.transformation import (
        DeepSeekAnthropicMessagesConfig,
    )

    original = DeepSeekAnthropicMessagesConfig.transform_anthropic_messages_request
    if getattr(original, "cheese_refills_thinking", False):
        return

    def transform(
        self,
        model,
        messages,
        anthropic_messages_optional_request_params,
        litellm_params,
        headers,
    ):
        request = original(
            self,
            model,
            messages,
            anthropic_messages_optional_request_params,
            litellm_params,
            headers,
        )
        return repair_request(request, model)

    transform.cheese_refills_thinking = True  # type: ignore[attr-defined]
    DeepSeekAnthropicMessagesConfig.transform_anthropic_messages_request = transform
