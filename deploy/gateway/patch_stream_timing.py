"""Preserve the request start time in the pinned LiteLLM streaming logger."""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/"
    "experimental_pass_through/messages/streaming_iterator.py"
)
expected = "f68b41bbeb04689c6dbe7ca5d924e7f7c515102fcd958a6ba89fc307b51b42b9"
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
    "3c216bd9722cd2c2739b2c2914b8fe6610ba48783894d658e9b41fc848a714e1"
), "Review the upstream HTTP handler before updating this patch"
marker = b"    async def post(\n"
assert http_source.count(marker) == 1
class_marker = b"class AsyncHTTPHandler:"
assert http_source.count(class_marker) == 1
http_path.write_bytes(
    http_source.replace(
        class_marker,
        b"from .provider_http_timing import trace_post\n\n\n" + class_marker,
    ).replace(marker, b"    @trace_post\n" + marker)
)

router_path = Path('/app/.venv/lib/python3.13/site-packages/litellm/router.py')
router_source = router_path.read_bytes()
assert hashlib.sha256(router_source).hexdigest() == (
    '4c11a670b62c001568c1ffbd6e1a85a46af67b1bd9311ec892a25ed3eb86fca4'
), 'Review the upstream router before updating this patch'
eager_debug = b'verbose_router_logger.debug(f"Inside ageneric_api_call_with_fallbacks() - model: {model}; kwargs: {kwargs}")'
lazy_debug = b'verbose_router_logger.debug("Inside ageneric_api_call_with_fallbacks() - model: %s; kwargs: %s", model, kwargs)'
assert router_source.count(eager_debug) == 1
router_path.write_bytes(router_source.replace(eager_debug, lazy_debug))
