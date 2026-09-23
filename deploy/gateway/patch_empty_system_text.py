"""Drop empty Anthropic system text blocks before provider dispatch."""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/experimental_pass_through/messages/handler.py"
)
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "535ae66486f29fa36c13cee1aa90cb695ec606baaccad5c45514280400d6c65f"
), "Review the upstream Anthropic Messages handler before updating this patch"

old_import = b"from litellm.llms.anthropic.common_utils import (\n"
new_import = old_import + b"    _is_empty_text_block,\n"
assert source.count(old_import) == 1
source = source.replace(old_import, new_import)

old = (
    b"    messages, system = AnthropicCacheControlHook.maybe_inject_cache_control(\n"
    b"        messages, system, kwargs, model=model, custom_llm_provider=custom_llm_provider, tools=tools\n"
    b"    )\n\n"
    b"    metadata = validate_anthropic_api_metadata(metadata)"
)
new = (
    b"    messages, system = AnthropicCacheControlHook.maybe_inject_cache_control(\n"
    b"        messages, system, kwargs, model=model, custom_llm_provider=custom_llm_provider, tools=tools\n"
    b"    )\n"
    b"    if isinstance(system, list):\n"
    b"        system = [block for block in system if not _is_empty_text_block(block)] or None\n\n"
    b"    metadata = validate_anthropic_api_metadata(metadata)"
)
assert source.count(old) == 1
path.write_bytes(source.replace(old, new))
