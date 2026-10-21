"""Deterministic display projections. The journal remains the only stored source."""

import json
import uuid
from datetime import datetime

from app.domain.session_record.preview import (
    _is_platform_tool,
    _short_tool_name,
    tool_preview,
)
from app.domain.session_record.service import encode_cursor


def text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text(v) for v in value)
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return ""


def _tool(name: str, args, call: str, *, output="", failed=False) -> dict:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"arguments": args}
    if not isinstance(args, dict):
        args = {"arguments": args}
    short = _short_tool_name(name)
    preview = tool_preview(short, args)
    return {
        "kind": "tool",
        "name": short,
        "call_id": call,
        "input": args,
        "preview": preview.text,
        "action": preview.action,
        "platform": _is_platform_tool(name, args),
        "text": output,
        "failed": failed,
    }


def parts(record: dict) -> list[dict]:
    """Readable parts with complete arguments and output; previews alone are capped."""
    if record.get("type") == "cheese_file":
        record = record.get("entry") or record.get("record") or record
    message = record.get("message")
    if not isinstance(message, dict):
        # A machine's note carries its sentence as a string; a harness record
        # carries a message object. Only the latter has content to read parts from.
        message = {}
    content = message.get("content")
    if isinstance(content, str):
        return [{"kind": "text", "text": content}]
    result = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            result.append({"kind": "text", "text": block.get("text", "")})
        elif kind in ("thinking", "redacted_thinking"):
            result.append(
                {
                    "kind": "thinking",
                    "text": block.get("thinking", "") if kind == "thinking" else "",
                }
            )
        elif kind == "tool_use":
            if isinstance(block.get("name"), str):
                result.append(
                    _tool(block["name"], block.get("input", {}), block.get("id", ""))
                )
        elif kind == "tool_result":
            result.append(
                {
                    "kind": "output",
                    "call_id": block.get("tool_use_id", ""),
                    "text": text(block.get("content")),
                    "failed": bool(block.get("is_error")),
                }
            )
            result.extend(_images(block.get("content")))
        elif kind == "image":
            result.append({"kind": "image", "source": block.get("source", {})})
    if record.get("type") == "result" and record.get("result"):
        result.append({"kind": "text", "text": record["result"]})
    if record.get("subtype") in (
        "api_retry",
        "compact_boundary",
        "sync_failed",
        "task_started",
        "task_notification",
    ):
        result.append(
            {
                "kind": "status",
                "label": record["subtype"],
                "text": text(record.get("message")),
            }
        )
    return result


def _images(content) -> list[dict]:
    return (
        [
            {"kind": "image", "source": b.get("source", {})}
            for b in content
            if isinstance(b, dict) and b.get("type") == "image"
        ]
        if isinstance(content, list)
        else []
    )


def as_block(item: dict, conversation_id: uuid.UUID) -> dict:
    record = item["record"]
    key = (
        datetime.fromisoformat(item["at"]),
        item["harness"],
        item["agent_handle"],
        item["sequence"],
    )
    cursor = encode_cursor(key)
    projected = parts(record)
    tool = next((p["name"] for p in projected if p["kind"] == "tool"), None)
    parent = record.get("parent_tool_use_id")
    if record.get("type") == "cheese_file":
        parent = record.get("agent_id") or (record.get("entry") or {}).get("agentId")
    return {
        "id": str(uuid.uuid5(conversation_id, cursor)),
        "conversation_id": str(conversation_id),
        "kind": "event",
        "author_type": "participant",
        "author": item["agent_handle"],
        "turn_id": item["turn_id"],
        "content": "\n".join(p.get("text", "") for p in projected),
        "created_at": item["at"],
        "updated_at": None,
        "refs": [],
        "reply_to": None,
        "meta": {
            "record": record,
            "parts": projected,
            "cursor": cursor,
            "sequence": item["sequence"],
            "harness": item["harness"],
            "parent": parent,
            "tool": tool,
            "narration": not tool,
            "in_room": False,
        },
    }
