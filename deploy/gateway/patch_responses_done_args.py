"""Carry function-call arguments that arrive only on the Responses `done` events.

The ChatGPT Codex backend sometimes streams a function call with no
`response.function_call_arguments.delta` at all: the full arguments appear only
on `response.function_call_arguments.done` and on the item of
`response.output_item.done`. The Messages adapter builds a tool_use input from
deltas alone, so such a call reached Claude Code as `{}` and failed schema
validation (Read without file_path, Bash without command). Measured on dev
2026-09-28..10-12: 21-78% of parallel GPT tool calls came back empty.

The patch emits the full arguments as one input_json_delta when a function-call
block closes without having received any delta. Blocks that did get deltas are
left alone, so arguments are never sent twice.
"""

import hashlib
from pathlib import Path

path = Path(
    "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/experimental_pass_through/responses_adapters/streaming_iterator.py"
)
source = path.read_bytes()
# The file as `patch_responses_stream_errors.py` leaves it, which runs first.
assert hashlib.sha256(source).hexdigest() == (
    "c2f6eea1cdc7200b6786215cee3772c452308f6d6f6b045aef557d0653ebb006"
), "Review the upstream Responses-to-Messages stream adapter and patch_responses_stream_errors.py before updating this patch"


def replace(old: bytes, new: bytes) -> None:
    global source
    assert source.count(old) == 1, old
    source = source.replace(old, new)


# Which function-call blocks are open, and which of them have received arguments.
replace(
    b"        self._pending_tool_ids: dict[str, str] = {}  # item_id -> call_id / name accumulator\n",
    b"        self._pending_tool_ids: dict[str, str] = {}  # item_id -> call_id / name accumulator\n"
    b"        self._tool_blocks: set[int] = set()\n"
    b"        self._tool_blocks_with_args: set[int] = set()\n",
)

# Remember the block a function call opens.
replace(
    b"                if item_id:\n"
    b"                    self._pending_tool_ids[item_id] = call_id\n"
    b"                self._open_block(\n",
    b"                if item_id:\n"
    b"                    self._pending_tool_ids[item_id] = call_id\n"
    b"                self._tool_blocks.add(self._current_block_index + 1)\n"
    b"                self._open_block(\n",
)

# A delta marks its block as having arguments.
replace(
    b"                else self._current_block_index\n"
    b"            )\n"
    b"            self._chunk_queue.append(\n"
    b"                {\n"
    b"                    \"type\": \"content_block_delta\",\n"
    b"                    \"index\": block_idx,\n"
    b"                    \"delta\": {\"type\": \"input_json_delta\", \"partial_json\": delta},\n",
    b"                else self._current_block_index\n"
    b"            )\n"
    b"            if delta:\n"
    b"                self._tool_blocks_with_args.add(block_idx)\n"
    b"            self._chunk_queue.append(\n"
    b"                {\n"
    b"                    \"type\": \"content_block_delta\",\n"
    b"                    \"index\": block_idx,\n"
    b"                    \"delta\": {\"type\": \"input_json_delta\", \"partial_json\": delta},\n",
)

# `arguments.done` and `output_item.done` both carry the full arguments; the
# first one seen for a block without deltas supplies them.
replace(
    b"        # ---- output item done -> content_block_stop ----\n"
    b"        if event_type == \"response.output_item.done\":\n",
    b"        if event_type == \"response.function_call_arguments.done\":\n"
    b"            item_id = self._field(event, \"item_id\")\n"
    b"            block_idx = (\n"
    b"                self._item_id_to_block_index.get(item_id, self._current_block_index)\n"
    b"                if isinstance(item_id, str) and item_id\n"
    b"                else self._current_block_index\n"
    b"            )\n"
    b"            self._supply_arguments(block_idx, self._field(event, \"arguments\"))\n"
    b"            return\n"
    b"\n"
    b"        # ---- output item done -> content_block_stop ----\n"
    b"        if event_type == \"response.output_item.done\":\n",
)
replace(
    b"            block_idx = self._item_id_to_block_index.get(item_id, -1) if item_id else self._current_block_index\n"
    b"            if block_idx < 0:\n"
    b"                return\n"
    b"            self._chunk_queue.append(\n"
    b"                {\n"
    b"                    \"type\": \"content_block_stop\",\n",
    b"            block_idx = self._item_id_to_block_index.get(item_id, -1) if item_id else self._current_block_index\n"
    b"            if block_idx < 0:\n"
    b"                return\n"
    b"            if self._field(item, \"type\") == \"function_call\":\n"
    b"                self._supply_arguments(block_idx, self._field(item, \"arguments\"))\n"
    b"            self._chunk_queue.append(\n"
    b"                {\n"
    b"                    \"type\": \"content_block_stop\",\n",
)

replace(
    b"    def _close_reasoning_item(self, item: object, item_id: str | None) -> None:\n",
    b"    def _supply_arguments(self, block_idx: int, arguments: object) -> None:\n"
    b"        if block_idx not in self._tool_blocks or block_idx in self._tool_blocks_with_args:\n"
    b"            return\n"
    b"        if not isinstance(arguments, str) or not arguments:\n"
    b"            return\n"
    b"        self._tool_blocks_with_args.add(block_idx)\n"
    b"        self._chunk_queue.append(\n"
    b"            {  # mutable-ok: API message payload\n"
    b"                \"type\": \"content_block_delta\",\n"
    b"                \"index\": block_idx,\n"
    b"                \"delta\": {\"type\": \"input_json_delta\", \"partial_json\": arguments},  # mutable-ok: API message payload\n"
    b"            }\n"
    b"        )\n"
    b"\n"
    b"    def _close_reasoning_item(self, item: object, item_id: str | None) -> None:\n",
)

path.write_bytes(source)
