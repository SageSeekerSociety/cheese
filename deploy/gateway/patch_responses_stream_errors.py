"""End a translated Responses stream with a retryable error when the upstream fails.

The pinned adapter that serves an OpenAI Responses model on /v1/messages logs
an upstream failure (a dropped connection, an in-stream `response.failed`) and
then ends the stream as if it had finished. The router has held back
`message_start` waiting for content, so the caller receives a 200 stream with
no content and no stop, which Claude Code reports as "Stream ended without
receiving any events" and does not retry. A clean HTTP EOF without a terminal
Responses event has the same outcome.

Emit a retryable streamed error for either: clean EOF uses
`overloaded_error`, while an upstream exception keeps its mapped status type
(or defaults to `api_error`). Claude Code retries a streamed error only once
the text block it was reading is closed, so a text block the failure left open
is closed with `content_block_stop` first. An open `tool_use`
block is never closed, because the caller would execute it instead of retrying;
content is never replayed, and no successful `message_stop` is manufactured.
"""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/"
    "experimental_pass_through/responses_adapters/streaming_iterator.py"
)
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "3058cf71891f9e3d957ed0c58300dac65571a1afd0623ca8aeba5dfaf3a95872"
), "Review the upstream Responses stream adapter before updating this patch"


def replace(old, new):
    global source
    assert source.count(old) == 1, old
    source = source.replace(old, new)


replace(
    b"        self._sync_responses_iterator: Iterator[object] | None = None\n",
    b"        self._sync_responses_iterator: Iterator[object] | None = None\n"
    b"        self._upstream_failed = False\n"
    b"        self._open_text_blocks: set[int] = set()\n",
)
replace(
    b"        # Consume the upstream stream\n        try:\n",
    b"        if self._upstream_failed:\n"
    b"            raise StopAsyncIteration\n\n"
    b"        # Consume the upstream stream\n        try:\n",
)
replace(
    b'            verbose_logger.error("AnthropicResponsesStreamWrapper error: %s\\n%s", e, traceback.format_exc())\n',
    b'            verbose_logger.error("AnthropicResponsesStreamWrapper error: %s\\n%s", e, traceback.format_exc())\n'
    b"            self._upstream_failed = True\n"
    b"            self._close_text_blocks()\n"
    b"            self._chunk_queue.append(_stream_error_event(e))\n",
)
replace(
    b"class AnthropicResponsesStreamWrapper:\n",
    b"def _stream_error_event(error: Exception | None = None) -> dict[str, object]:\n"
    b"    if error is None:\n"
    b"        return {\"type\": \"error\", \"error\": {\"type\": \"overloaded_error\", \"message\": \"upstream stream ended before completion\"}}\n"
    b"    from litellm.llms.anthropic.common_utils import ANTHROPIC_ERROR_STATUS_CODE_MAP\n\n"
    b"    cause: Final = getattr(error, \"original_exception\", None) or error\n"
    b"    status: Final = getattr(cause, \"status_code\", None)\n"
    b"    error_type: Final = next(\n"
    b"        (name for name, code in ANTHROPIC_ERROR_STATUS_CODE_MAP.items() if code == status), \"api_error\"\n"
    b"    )\n"
    b"    return {\"type\": \"error\", \"error\": {\"type\": error_type, \"message\": str(cause) or type(cause).__name__}}\n\n\n"
    b"class AnthropicResponsesStreamWrapper:\n",
)
replace(
    b"    def _open_block(self, item_id: str | None, content_block: Mapping[str, object]) -> int:\n"
    b"        block_idx = self._next_block_index()\n"
    b"        if item_id:\n"
    b"            self._item_id_to_block_index[item_id] = block_idx\n"
    b"        self._chunk_queue.append(\n"
    b"            {\n"
    b'                "type": "content_block_start",\n'
    b'                "index": block_idx,\n'
    b'                "content_block": content_block,\n'
    b"            }\n"
    b"        )\n"
    b"        return block_idx\n",
    b"    def _open_block(self, item_id: str | None, content_block: Mapping[str, object]) -> int:\n"
    b"        block_idx = self._next_block_index()\n"
    b"        if item_id:\n"
    b"            self._item_id_to_block_index[item_id] = block_idx\n"
    b'        if content_block.get("type") == "text":\n'
    b"            self._open_text_blocks.add(block_idx)\n"
    b"        self._chunk_queue.append(\n"
    b"            {\n"
    b'                "type": "content_block_start",\n'
    b'                "index": block_idx,\n'
    b'                "content_block": content_block,\n'
    b"            }\n"
    b"        )\n"
    b"        return block_idx\n\n"
    b"    def _close_text_blocks(self) -> None:\n"
    b'        """Close a text block an upstream failure left open so the caller can retry.\n\n'
    b"        Only a text block is closed: a `content_block_stop` on a `tool_use`\n"
    b"        block would make the caller run the tool instead of retrying.\n"
    b'        """\n'
    b"        for index in sorted(self._open_text_blocks):\n"
    b'            self._chunk_queue.append({"type": "content_block_stop", "index": index})\n'
    b"        self._open_text_blocks.clear()\n",
)
replace(
    b"            block_idx = self._item_id_to_block_index.get(item_id, -1) if item_id else self._current_block_index\n"
    b"            if block_idx < 0:\n"
    b"                return\n"
    b"            self._chunk_queue.append(\n"
    b"                {\n"
    b'                    "type": "content_block_stop",\n'
    b'                    "index": block_idx,\n'
    b"                }\n"
    b"            )\n"
    b"            return\n",
    b"            block_idx = self._item_id_to_block_index.get(item_id, -1) if item_id else self._current_block_index\n"
    b"            if block_idx < 0:\n"
    b"                return\n"
    b"            self._open_text_blocks.discard(block_idx)\n"
    b"            self._chunk_queue.append(\n"
    b"                {\n"
    b'                    "type": "content_block_stop",\n'
    b'                    "index": block_idx,\n'
    b"                }\n"
    b"            )\n"
    b"            return\n",
)
replace(
    b"        # Drain any remaining queued chunks\n",
    b"        # HTTP EOF is not a completed model response. Only a terminal\n"
    b"        # Responses event may finish the message, including max_tokens.\n"
    b"        if not self._sent_message_stop and not self._upstream_failed:\n"
    b"            self._upstream_failed = True\n"
    b"            self._close_text_blocks()\n"
    b"            self._chunk_queue.append(_stream_error_event())\n\n"
    b"        # Drain any remaining queued chunks\n",
)
path.write_bytes(source)
