"""Price every search a DeepSeek answer reports, not one per answer.

DeepSeek's Messages API runs the server web search tool Claude Code's WebSearch
sends and reports how many searches it ran in
``usage.server_tool_use.web_search_requests``, which is what it bills: $0.75
per 1,000 successful requests (https://api-docs.deepseek.com/guides/search/).
The pinned cost calculator reads that count only for Anthropic, Gemini, xAI,
Vertex and Groq; for ``deepseek`` it falls through to the per-call path, which
charges one search per answer however many ran (measured on 1.103.3: an
answer reporting 3 searches was charged for 1). DeepSeek reports the count
exactly as Anthropic does, so it takes Anthropic's calculator.
"""

import hashlib
from pathlib import Path

path = Path("/app/.venv/lib/python3.13/site-packages/litellm/llms/__init__.py")
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "578bd141c3bc6b1c0aaa09efcba446e054afeb9a9b0a42bb26f21c31c1fb105d"
), "Review the upstream web search cost routing before updating this patch"
old = b'    elif custom_llm_provider == "perplexity":\n'
new = (
    b'    elif custom_llm_provider == "deepseek":\n'
    b"        from .anthropic.cost_calculation import get_cost_for_anthropic_web_search\n"
    b"\n"
    b"        return get_cost_for_anthropic_web_search(model_info=model_info, usage=usage)\n"
) + old
assert source.count(old) == 1
path.write_bytes(source.replace(old, new))
