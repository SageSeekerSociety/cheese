"""Turn Claude Code's stream-json records into the room's event vocabulary.

The records are what the runner journaled: the lines Claude Code wrote to
stdout about what the session did, plus the transcript lines of agents only
their own file reports on (``cheese_file``). One assistant record may carry
several content blocks, so the events a record comes out as each get their own
id: the record's ``uuid`` (Claude Code's, stable across a re-read) plus the
block's index.

What a call was (its name and description) rides on the record that brings its
result back: the runner saw the call made and stamps it there (``cheese.calls``),
so a record reads the same whenever and wherever it is read.

How the work ended is not read here: the runner reports it in the lifecycle
protocol (`driven/lifecycle.py`), and a ``result`` record is only something the
session wrote.

A tool's return stays in the raw journal; a failed one marks its call as
failed (``AgentStepFailed``). A subagent's return is its conclusion, which
otherwise reaches only the thread that spawned it, so it becomes an event of
its own.
"""

import re
from datetime import datetime

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.service import (
    AgentCompacting,
    AgentEvent,
    AgentMessage,
    AgentRetrying,
    AgentSessionInfo,
    AgentStepFailed,
    AgentToolResult,
    AgentToolUse,
    step_error,
)

#: The tools whose RETURN the room needs: a subagent reports only to whoever
#: spawned it.
SUBAGENT_TOOLS = {"Agent", "Task"}
#: What the build writes into a tool result a person stopped. A person pressing
#: stop is not a tool going wrong, and painting it red would say something broke.
INTERRUPTED = "interrupted by user"
#: The line the build puts above a failed command's own output. The step is
#: already marked failed; what the room needs is what the command said.
EXIT_HEADER = re.compile(r"\AExit code \d+\n(?=\S)")
#: The tag the build wraps a refused or failed call's text in, for the model.
#: The room is shown the text.
TOOL_USE_ERROR = re.compile(r"\A\s*<tool_use_error>(.*)</tool_use_error>\s*\Z", re.S)


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _at(record: dict) -> datetime | None:
    stamp = record.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _authored(
    events: list[AgentEvent], record: dict, session_id: str | None = None
) -> list[AgentEvent]:
    """Name who wrote these, from the runner's stamp on the record.

    Every record carries it, not just the init and the result. Read only there,
    the words and calls in between were signed by whatever the backend holding
    the turn remembered — and a backend that took the turn over during a deploy
    remembers nothing, so it signed them as the room's default agent.

    The record's own conversation id rides along too (FB-56 legacy③): a turn
    the session started on its own opens on one of these events, and its row
    is stamped with it — the only way that row is later attributable to this
    conversation's death evidence, and to no other's.
    """
    handle = (record.get("cheese") or {}).get("agent_handle")
    conversation = str(record.get("session_id") or "") or session_id
    for event in events:
        if isinstance(event, AgentMessage | AgentToolUse | AgentToolResult):
            if handle:
                event.agent_handle = event.agent_handle or handle
            if conversation and isinstance(event, AgentMessage | AgentToolUse):
                event.session_id = event.session_id or conversation
    return events


def _retrying(record: dict) -> AgentRetrying:
    """``system/api_retry``, as the pinned build writes it: ``attempt``,
    ``max_retries``, ``retry_delay_ms``, ``error_status`` (null for a
    connection error), ``error``, the kind of failure, and — only when the
    request got no response at all — ``no_response.waited_ms``."""
    no_response = record.get("no_response")
    return AgentRetrying(
        error=str(record.get("error") or ""),
        attempt=_count(record.get("attempt")),
        max_attempts=_count(record.get("max_retries")),
        delay_ms=_count(record.get("retry_delay_ms")),
        status=_count(record.get("error_status")),
        no_response_ms=_count(no_response.get("waited_ms"))
        if isinstance(no_response, dict)
        else None,
    )


def _compacting(record: dict) -> list[AgentEvent]:
    """``system/status``, as the pinned build writes it around a compaction:
    ``status: "compacting"`` when it starts, then ``status: null`` with
    ``compact_result`` (``"success"`` / ``"failed"``) and, on failure,
    ``compact_error``. The same record also reports other status changes
    (a permission mode), which carry neither and say nothing to the room."""
    if record.get("status") == "compacting":
        return [AgentCompacting()]
    result = record.get("compact_result")
    if result not in ("success", "failed"):
        return []
    error = str(record.get("compact_error") or "failed") if result == "failed" else ""
    return [AgentCompacting(done=True, error=error)]


def _message(record: dict) -> dict:
    """The transcript-shaped part of a record: the record itself, or the entry
    of an agent's own transcript file it carries."""
    if record.get("type") == "cheese_file":
        return record.get("entry") or {}
    return record


class Assembler:
    """Records in, room events out. Holds nothing between records."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id

    def accept(self, record: dict) -> list[AgentEvent]:
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "init":
            stamp = record.get("cheese") or {}
            return [
                AgentSessionInfo(
                    session_id=str(record.get("session_id")),
                    agent_handle=stamp.get("agent_handle"),
                    harness=CLAUDE_CODE,
                )
            ]
        if kind == "system":
            return self._system(record)
        message = _message(record)
        if message.get("type") == "assistant":
            return _authored(self._assistant(message), record, self.session_id)
        if message.get("type") == "user" and not message.get("isReplay"):
            calls = (record.get("cheese") or {}).get("calls") or {}
            return _authored(self._returned(message, calls), record, self.session_id)
        return []

    def _assistant(self, message: dict) -> list[AgentEvent]:
        if message.get("error"):
            # The build's own line for an API error. The turn's result reports
            # the failure; this would be the same news as a message from 芝士.
            return []
        events: list[AgentEvent] = []
        for index, block in enumerate(
            (message.get("message") or {}).get("content") or []
        ):
            if not isinstance(block, dict):
                continue
            eid = f"claude:{message.get('uuid')}:{index}"
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                events.append(
                    AgentMessage(
                        block["text"],
                        eid=eid,
                        eids=(eid,),
                        at=_at(message),
                    )
                )
            elif block.get("type") == "tool_use":
                arguments = block.get("input")
                events.append(
                    AgentToolUse(
                        str(block.get("name") or ""),
                        arguments if isinstance(arguments, dict) else {},
                        eid=eid,
                        call_id=str(block.get("id")),
                        at=_at(message),
                    )
                )
        return events

    def _returned(self, message: dict, calls: dict) -> list[AgentEvent]:
        content = (message.get("message") or {}).get("content")
        events: list[AgentEvent] = []
        for index, block in enumerate(content if isinstance(content, list) else []):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call = str(block.get("tool_use_id"))
            said = _text(block.get("content"))
            made = calls.get(call)
            if not isinstance(made, dict):
                made = {}
            if block.get("is_error"):
                said = TOOL_USE_ERROR.sub(r"\1", said)
                if INTERRUPTED in said:
                    continue
                events.append(
                    AgentStepFailed(
                        call_id=call,
                        text=step_error(EXIT_HEADER.sub("", said)),
                    )
                )
            if made.get("name") not in SUBAGENT_TOOLS or block.get("is_error"):
                continue
            result = message.get("tool_use_result")
            report = (
                _text(result.get("content")) if isinstance(result, dict) else ""
            ) or said
            if report.strip():
                events.append(
                    AgentToolResult(
                        name=made["name"],
                        text=report.strip(),
                        description=made.get("description", ""),
                        eid=f"claude:{message.get('uuid')}:{index}",
                    )
                )
        return events

    def _system(self, record: dict) -> list[AgentEvent]:
        subtype = record.get("subtype")
        if subtype == "api_retry":
            return [_retrying(record)]
        if subtype == "status":
            return _compacting(record)
        return []
