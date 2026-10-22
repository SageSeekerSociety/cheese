"""Deterministic display projections. The journal remains the only stored source.

Three harnesses journal three shapes — Claude Code's stream-json messages, Codex's
app-server notifications, pi's session entries — and `parts` is the one place that
turns any of them into the same readable parts. Both readers of a record go through
it: the 现场 timeline (`as_block`, below) and a transcript file (`line`), so a
reader of one and a reader of the other see the same things.

Why this package knows the harness shapes rather than importing them: it is the
bottom layer (`backend/.importlinter`, C5), and `app.domain.agent` sits three layers
above it. The names are the record conventions, not imports — the same call `history`
makes when it reads `agent_turns` bare.
"""

import json
import uuid
from datetime import datetime

from app.domain.session_record.preview import (
    _is_platform_tool,
    _short_tool_name,
    tool_preview,
)
from app.domain.session_record.service import encode_cursor

#: What pi's runner writes into the session log for what only its live stream
#: knows (`agent/harness/pi/journal.py`): a retry, giving up, compaction. Spelled
#: out here because this package cannot import that one.
_PI_STATUS = {
    "cheese_retrying": "retrying",
    "cheese_gave_up": "gave up",
    "cheese_compacting": "compacting",
}


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
    """Readable parts with complete arguments and output; previews alone are capped.

    One shape for all three harnesses: `text`, `thinking`, `tool`, `output`,
    `image` and `status`, which is what 现场 renders and what `line` writes out.
    """
    if record.get("type") == "cheese_file":
        record = record.get("entry") or record.get("record") or record
    if isinstance(record.get("method"), str):
        return _codex_parts(record)
    if record.get("type") in _PI_STATUS:
        return [
            {
                "kind": "status",
                "label": _PI_STATUS[record["type"]],
                "text": text(record.get("errorMessage")),
            }
        ]
    message = record.get("message")
    if not isinstance(message, dict):
        # A machine's note carries its sentence as a string; a harness record
        # carries a message object. Only the latter has content to read parts from.
        message = {}
    if message.get("role") == "toolResult":
        # pi keeps a tool's return as a message of its own. Reading it through
        # the content loop below would call it prose.
        return [
            {
                "kind": "output",
                "call_id": message.get("toolCallId", ""),
                "text": text(message.get("content")),
                "failed": bool(message.get("isError")),
            },
            *_images(message.get("content")),
        ]
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
        elif kind == "toolCall":
            # pi's name for the same thing Claude Code writes as `tool_use`.
            if isinstance(block.get("name"), str):
                result.append(
                    _tool(
                        block["name"],
                        block.get("arguments", {}),
                        block.get("id", ""),
                    )
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


def _said(value) -> str:
    """The prose in a list of strings or of content parts (`summary`)."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "\n".join(
        item if isinstance(item, str) else str(item.get("text") or "")
        for item in value
        if isinstance(item, (str, dict))
    ).strip()


def _codex_parts(record: dict) -> list[dict]:
    """One app-server notification as parts.

    Codex reports a step twice: `item/started` carries the call and
    `item/completed` the whole of it. Only the completed item is read: it has
    the call's name and arguments as well as its return, so one row can say both
    what was run and what came back — the same pair Claude Code journals as
    `tool_use` and `tool_result`, and what a started item would otherwise have
    said twice.
    """
    method = record.get("method")
    params = record.get("params") or {}
    if method == "error" and params.get("willRetry"):
        return [
            {
                "kind": "status",
                "label": "retrying",
                "text": str((params.get("error") or {}).get("message") or ""),
            }
        ]
    if method != "item/completed":
        return []
    item = params.get("item")
    if not isinstance(item, dict):
        return []
    kind = item.get("type")
    if kind == "reasoning":
        said = _said(item.get("summary")) or _said(item.get("content"))
        return [{"kind": "thinking", "text": said}] if said else []
    if kind == "agentMessage":
        said = str(item.get("text") or "")
        return [{"kind": "text", "text": said}] if said else []
    if kind == "contextCompaction":
        return [{"kind": "status", "label": "compaction", "text": ""}]
    if kind in ("dynamicToolCall", "mcpToolCall"):
        call = str(item.get("id") or "")
        if kind == "mcpToolCall":
            name = f"mcp__{item.get('server')}__{item.get('tool')}"
            result = item.get("result")
            returned = _said(result.get("content")) if isinstance(result, dict) else ""
            error = item.get("error")
            if isinstance(error, dict) and not returned:
                returned = str(error.get("message") or "")
        else:
            name = str(item.get("tool") or "")
            returned = _said(
                [
                    part
                    for part in item.get("contentItems") or []
                    if isinstance(part, dict) and part.get("type") == "inputText"
                ]
            )
        failed = item.get("success") is False or item.get("status") == "failed"
        if isinstance(item.get("error"), dict):
            failed = True
        parts = [_tool(name, item.get("arguments") or {}, call)] if name else []
        parts.append(
            {"kind": "output", "call_id": call, "text": returned, "failed": failed}
        )
        return parts
    return []


def thread_of(record: dict) -> str | None:
    """Which thread wrote this record, where the harness says.

    A subagent's records are in the same conversation as its session's. The 现场
    tree and a transcript's per-turn summary both need to tell them apart, and
    each harness names the thread differently.
    """
    if record.get("type") == "cheese_file":
        agent = record.get("agent_id") or (record.get("entry") or {}).get("agentId")
        return agent if isinstance(agent, str) else None
    if isinstance(record.get("method"), str):
        params = record.get("params") or {}
        if record["method"] == "thread/started":
            thread = params.get("thread") or {}
            started = thread.get("id")
            return started if isinstance(started, str) else None
        named = params.get("threadId")
        return named if isinstance(named, str) else None
    pending = record.get("parent_tool_use_id")
    if isinstance(pending, str):
        return pending
    thread = record.get("subagent")
    if isinstance(thread, dict) and isinstance(thread.get("id"), str):
        return thread["id"]
    return None


def parent_thread(record: dict) -> str | None:
    """The thread that started this one, for the record that says so (Codex)."""
    if record.get("method") != "thread/started":
        return None
    thread = (record.get("params") or {}).get("thread") or {}
    parent = thread.get("parentThreadId")
    return parent if isinstance(parent, str) else None


def role_of(record: dict) -> str | None:
    """Whose words these are, where the shape says: `user` or `assistant`."""
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("role"), str):
        return message["role"]
    if record.get("type") in ("user", "assistant"):
        return record["type"]
    return None


def _carries_a_subagent(record: dict) -> bool:
    """Whether the harness marked this very record as a subagent's.

    Claude Code marks the record (`parent_tool_use_id`, or a side file's
    `agent_id`) and pi marks it (`subagent`). Codex marks the *thread* instead,
    in the record that started it — that half is `lines` below, which can see
    the record that did the declaring.
    """
    if record.get("type") == "cheese_file":
        entry = record.get("entry") or {}
        return bool(record.get("agent_id") or entry.get("agentId"))
    if record.get("parent_tool_use_id") is not None:
        return True
    return isinstance(record.get("subagent"), dict)


def line(item: dict, *, subagent: bool) -> dict:
    """One record as a line of a transcript file: the same keys for every harness.

    `parts` is the 现场 projection, so the file and the timeline say the same
    things about the same record. `subagent` is the one field a reader needs to
    keep a spawner's own steps apart from what the thing it spawned did;
    `thread` and `parent_thread` name which thread that was.

    `subagent` is decided once for the whole read (`lines`) rather than here,
    because one harness declares a child thread in a record of its own.
    """
    record = item["record"]
    return {
        "seq": item["sequence"],
        "at": item["at"],
        "turn": item["turn_id"],
        "author": item["agent_handle"],
        "harness": item["harness"],
        "kind": record.get("method") or record.get("type") or "",
        "role": role_of(record),
        "thread": thread_of(record),
        "parent_thread": parent_thread(record),
        "subagent": subagent,
        "parts": parts(record),
        # Set on a channel-wide read, which is the only one that mixes
        # conversations; null in a single conversation's file, where every row
        # would repeat the same value. One shape either way.
        "conversation": item.get("conversation_id"),
    }


def lines(items: list[dict]) -> list[dict]:
    """A page of records as transcript rows, subagents marked.

    A subagent writes into the same conversation as the session that started
    it, so its rows have to be told apart before they can be left out of that
    session's own step count. Claude Code and pi say so on each record; Codex
    says which thread is a child in the record that started it, and the rest of
    that thread's records only carry the thread's id. Both halves are read here,
    where the whole page is in hand.
    """
    declared = {
        thread_of(item["record"]) for item in items if parent_thread(item["record"])
    }
    declared.discard(None)
    return [
        line(
            item,
            subagent=_carries_a_subagent(item["record"])
            or thread_of(item["record"]) in declared,
        )
        for item in items
    ]


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
            # The machine's own note is not a step of a turn: it counts in no
            # turn's steps. A note with no turn is drawn as a platform line of
            # its own; a checkpoint's carries one and joins that turn.
            "platform": item["harness"] == "machine",
            "in_room": False,
        },
    }
