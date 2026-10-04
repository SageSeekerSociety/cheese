"""Drop empty Anthropic system and nested tool-result text blocks."""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/experimental_pass_through/messages/handler.py"
)
source = path.read_bytes()
assert hashlib.sha256(source).hexdigest() == (
    "84444031e17973bbd6273ba029e51a063b58ba98eea7f214900fcc427825e87b"
), "Review the upstream Anthropic Messages handler before updating this patch"

old_import = b"from litellm.llms.anthropic.common_utils import (\n"
new_import = old_import + b"    _is_empty_text_block,\n"
assert source.count(old_import) == 1
source = source.replace(old_import, new_import)

old = (
    b"    messages, system = AnthropicCacheControlHook.maybe_inject_cache_control(\n"
    b"        messages, system, kwargs, model=model, custom_llm_provider=custom_llm_provider, tools=tools, api_base=api_base\n"
    b"    )\n\n"
    b"    metadata = validate_anthropic_api_metadata(metadata)"
)
new = (
    b"    messages, system = AnthropicCacheControlHook.maybe_inject_cache_control(\n"
    b"        messages, system, kwargs, model=model, custom_llm_provider=custom_llm_provider, tools=tools, api_base=api_base\n"
    b"    )\n"
    b"    if isinstance(system, list):\n"
    b"        system = [block for block in system if not _is_empty_text_block(block)] or None\n\n"
    b"    metadata = validate_anthropic_api_metadata(metadata)"
)
assert source.count(old) == 1
path.write_bytes(source.replace(old, new))

common_path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/common_utils.py"
)
common_source = common_path.read_bytes()
assert hashlib.sha256(common_source).hexdigest() == (
    "08940a9f25848bc0519dda4096d6c0bcc4462c6eb1364ccc1b2485f93f200eac"
), "Review the upstream Anthropic Messages sanitizer before updating this patch"
old_filter = b"        filtered = [b for b in content if not _is_empty_text_block(b) and not is_empty_thinking_block(b)]"
new_filter = (
    b"        filtered = [\n"
    b"            _strip_empty_tool_result_text(b)\n"
    b"            for b in content if not _is_empty_text_block(b) and not is_empty_thinking_block(b)\n"
    b"        ]"
)
assert common_source.count(old_filter) == 1
common_source = common_source.replace(old_filter, new_filter)
old_unchanged = b"        if len(filtered) == len(content):\n"
assert common_source.count(old_unchanged) == 1
common_source = common_source.replace(old_unchanged, b"        if filtered == content:\n")
helper_marker = b"def _is_empty_text_block(block: object) -> bool:\n"
helper = (
    b"def _strip_empty_tool_result_text(block: Any) -> Any:\n"
    b"    if not isinstance(block, dict) or block.get(\"type\") != \"tool_result\":\n"
    b"        return block\n"
    b"    nested = block.get(\"content\")\n"
    b"    if not isinstance(nested, list):\n"
    b"        return block\n"
    b"    filtered = [item for item in nested if not _is_empty_text_block(item)]\n"
    b"    # Keep an all-empty result unchanged; it has no truthful replacement text.\n"
    b"    return {**block, \"content\": filtered} if filtered and len(filtered) < len(nested) else block\n\n\n"
)
assert common_source.count(helper_marker) == 1
common_path.write_bytes(common_source.replace(helper_marker, helper + helper_marker))
