"""Send an assistant message's reasoning back on a hosted_vllm/ chat route.

The pinned adapter removes `reasoning_content` from every assistant message.
A thinking model behind chat/completions needs it back on the turn it is
still working through: DeepSeek in thinking mode answers 400 "The
`content[].thinking` in the thinking mode must be passed back" when a tool
result follows an assistant message without it. `thinking_blocks` is
LiteLLM's own field, which no chat upstream reads, so it is still removed.
"""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/hosted_vllm/chat/transformation.py"
)
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "52348955e21fd0bff3dfa5941c3f554cdacc3f0bcb32df22ac92152acbd81c28"
), "Review the upstream hosted_vllm adapter before updating this patch"
old = (
    b'                message.pop("thinking_blocks", None)\n'
    b'                message.pop("reasoning_content", None)\n'
)
new = b'                message.pop("thinking_blocks", None)\n'
assert source.count(old) == 1
path.write_bytes(source.replace(old, new))
