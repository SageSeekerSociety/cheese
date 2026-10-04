"""Preserve the request start time in the pinned LiteLLM streaming logger."""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/"
    "experimental_pass_through/messages/streaming_iterator.py"
)
expected = "6a03f919a3c45635adba2d928ffbce01e418f7d1d92da59841565b4eb32e5649"
original = path.read_bytes()
assert hashlib.sha256(original).hexdigest() == expected, (
    "Review the upstream file before updating this patch"
)
old = b"self.start_time = datetime.now()"
new = b"self.start_time = litellm_logging_obj.start_time"
assert original.count(old) == 1
path.write_bytes(original.replace(old, new))

http_path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/http_handler.py"
)
http_source = http_path.read_bytes()
assert hashlib.sha256(http_source).hexdigest() == (
    "cd2d0a14dc9e8fb94564aece7578fa04a37569d60a177da1b33a9686d5676cfe"
), "Review the upstream HTTP handler before updating this patch"
marker = b"    async def post(\n"
assert http_source.count(marker) == 1
class_marker = b"class AsyncHTTPHandler:"
assert http_source.count(class_marker) == 1
# Only POST supplies model streams; transfer retry-client ownership to the body.
post_start = http_source.index(marker)
post_end = http_source.index(b"    async def put(", post_start)
post_source = http_source[post_start:post_end]
assert post_source.count(b"return await self.single_connection_post_request(") == 1
assert post_source.count(b"            finally:\n                await new_client.aclose()") == 1
post_source = post_source.replace(
    b"return await self.single_connection_post_request(",
    b"response = await self.single_connection_post_request(",
).replace(
    b"            finally:\n                await new_client.aclose()",
    b"            except BaseException:\n"
    b"                await new_client.aclose()\n"
    b"                raise\n"
    b"            if stream:\n"
    b"                response.stream = ClientOwnedStream(response.stream, new_client)\n"
    b"            else:\n"
    b"                await new_client.aclose()\n"
    b"            return response",
)
http_source = http_source[:post_start] + post_source + http_source[post_end:]
http_path.write_bytes(
    http_source.replace(
        class_marker,
        b"from .provider_http_timing import trace_post\n"
        b"from .retry_stream import ClientOwnedStream\n\n\n" + class_marker,
    ).replace(marker, b"    @trace_post\n" + marker)
)
