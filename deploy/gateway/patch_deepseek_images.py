"""Retain image content in tool results for vision models in the pinned DeepSeek adapter.

Upstream forwards image_url blocks only in user messages. An image Claude Code
reads from disk arrives inside a tool_result, which the Anthropic adapter turns
into a tool message, so upstream collapses it to text and drops the picture.
"""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/deepseek/chat/transformation.py"
)
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "5c6de1e9ca6da5ce28a272aae0de9218c997e8478cf83ec9787c44ebf90c800a"
), "Review the upstream DeepSeek adapter before updating this patch"
old = b'        if message.get("role") != "user":\n            return False\n'
new = b'        if message.get("role") not in ("user", "tool"):\n            return False\n'
assert source.count(old) == 1
path.write_bytes(source.replace(old, new))
