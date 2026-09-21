"""Retain image content for vision models in the pinned DeepSeek adapter."""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/deepseek/chat/transformation.py"
)
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "b9603258b0aecd7657fc83181f8221036beab3527fb0363cb7d13ef4684144a9"
), "Review the upstream DeepSeek adapter before updating this patch"
old = (
    b"        messages = handle_messages_with_content_list_to_str_conversion(messages)"
)
new = (
    b'        if not litellm.supports_vision(model=model, custom_llm_provider="deepseek"):\n'
    b"    " + old
)
assert source.count(old) == 1
path.write_bytes(source.replace(old, new))
