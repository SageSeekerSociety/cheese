"""End a translated Responses stream with an error event when the upstream fails.

The pinned adapter that serves an OpenAI Responses model on /v1/messages logs
an upstream failure (a dropped connection, an in-stream `response.failed`) and
then ends the stream as if it had finished. The router has held back
`message_start` waiting for content, so the caller receives a 200 stream with
no content and no stop, which Claude Code reports as "Stream ended without
receiving any events" and does not retry.
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
    b"        self._upstream_failed = False\n",
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
    b"            self._chunk_queue.append(_stream_error_event(e))\n",
)
replace(
    b"class AnthropicResponsesStreamWrapper:\n",
    b"def _stream_error_event(error: Exception) -> dict[str, object]:\n"
    b"    from litellm.llms.anthropic.common_utils import ANTHROPIC_ERROR_STATUS_CODE_MAP\n\n"
    b"    cause: Final = getattr(error, \"original_exception\", None) or error\n"
    b"    status: Final = getattr(cause, \"status_code\", None)\n"
    b"    error_type: Final = next(\n"
    b"        (name for name, code in ANTHROPIC_ERROR_STATUS_CODE_MAP.items() if code == status), \"api_error\"\n"
    b"    )\n"
    b"    return {\"type\": \"error\", \"error\": {\"type\": error_type, \"message\": str(cause) or type(cause).__name__}}\n\n\n"
    b"class AnthropicResponsesStreamWrapper:\n",
)
path.write_bytes(source)
