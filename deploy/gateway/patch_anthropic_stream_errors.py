"""End a /v1/messages stream that fails after it started with an Anthropic error event.

When an upstream fails mid-stream and no fallback is configured, the router
re-raises the failure to the proxy, which ends the stream with an
OpenAI-shaped `data: {"error": ...}` frame that carries no `event:` name. If
the gateway has already answered 200 (a keepalive ping goes out after 15 s of
waiting), an Anthropic client never sees that frame: Claude Code reads only
named events, reports "Stream ended without receiving any events" and ends the
turn instead of retrying. The first chunk is left alone, because the proxy
already answers an error there with its HTTP status, which callers retry.
"""

import hashlib
from pathlib import Path

path = Path("/app/.venv/lib/python3.13/site-packages/litellm/proxy/common_request_processing.py")
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "851c95005eb4966a849ede7db5dc45304d5c66d4f96a7059229d01032d9c8180"
), "Review the upstream proxy stream error handling before updating this patch"


def replace(old, new):
    global source
    assert source.count(old) == 1, old
    source = source.replace(old, new)


replace(
    b"                        selected_data_generator = wrap_sse_stream_with_keepalive_pings(\n"
    b"                            stream=selected_data_generator,\n"
    b"                            ping_interval_seconds=litellm.anthropic_sse_ping_interval_seconds,\n"
    b"                        )\n",
    b"                        selected_data_generator = wrap_sse_stream_with_keepalive_pings(\n"
    b"                            stream=selected_data_generator,\n"
    b"                            ping_interval_seconds=litellm.anthropic_sse_ping_interval_seconds,\n"
    b"                        )\n"
    b"                        selected_data_generator = _anthropic_errors_after_first_chunk(selected_data_generator)\n",
)
replace(
    b"class _UpstreamClosingStreamingResponse(StreamingResponse):\n",
    b"_ANTHROPIC_STREAM_ERROR_TYPES: Final = MappingProxyType(\n"
    b"    {\n"
    b"        400: \"invalid_request_error\",\n"
    b"        401: \"authentication_error\",\n"
    b"        403: \"permission_error\",\n"
    b"        404: \"not_found_error\",\n"
    b"        413: \"request_too_large\",\n"
    b"        429: \"rate_limit_error\",\n"
    b"    }\n"
    b")\n\n\n"
    b"async def _anthropic_errors_after_first_chunk(stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:\n"
    b"    \"\"\"Re-frame an error that arrives after the first chunk as an Anthropic `event: error`.\"\"\"\n"
    b"    try:\n"
    b"        first = True\n"
    b"        async for chunk in stream:\n"
    b"            status_code = None if first else await _parse_event_data_for_error(chunk)\n"
    b"            first = False\n"
    b"            if status_code is None:\n"
    b"                yield chunk\n"
    b"                continue\n"
    b"            # A server-side failure is sent as overloaded_error, not api_error, even for a 500 or a\n"
    b"            # dropped connection: it is the only streamed error type Claude Code retries.\n"
    b"            error_type = _ANTHROPIC_STREAM_ERROR_TYPES.get(\n"
    b"                status_code, \"overloaded_error\" if status_code >= 500 else \"invalid_request_error\"\n"
    b"            )\n"
    b"            message = str(_extract_error_from_sse_chunk(chunk).get(\"message\") or \"\")\n"
    b"            body = {\"type\": \"error\", \"error\": {\"type\": error_type, \"message\": message}}\n"
    b"            yield f\"event: error\\ndata: {json.dumps(body)}\\n\\n\"\n"
    b"    finally:\n"
    b"        with anyio.CancelScope(shield=True), contextlib.suppress(BaseException):\n"
    b"            await stream.aclose()\n\n\n"
    b"class _UpstreamClosingStreamingResponse(StreamingResponse):\n",
)
path.write_bytes(source)
