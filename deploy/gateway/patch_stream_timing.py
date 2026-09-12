"""Preserve the request start time in the pinned LiteLLM streaming logger."""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/"
    "experimental_pass_through/messages/streaming_iterator.py"
)
expected = "f68b41bbeb04689c6dbe7ca5d924e7f7c515102fcd958a6ba89fc307b51b42b9"
original = path.read_bytes()
assert hashlib.sha256(original).hexdigest() == expected, "Review the upstream file before updating this patch"
old = b"self.start_time = datetime.now()"
new = b"self.start_time = litellm_logging_obj.start_time"
assert original.count(old) == 1
path.write_bytes(original.replace(old, new))
