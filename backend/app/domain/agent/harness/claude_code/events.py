"""Turn Claude Code's stream-json records into the room's event vocabulary.

The records are what the runner journaled: the lines Claude Code wrote to
stdout about what the session did, plus the transcript lines of agents only
their own file reports on (``cheese_file``). One assistant record may carry
several content blocks, so the events a record comes out as each get their own
id: the record's ``uuid`` (Claude Code's, stable across a re-read) plus the
block's index.

What a call was (its name and description) is a ``fact``: worked out once, in
journal order, when a page is mirrored (``bind``), and read back by every later
pass.

A tool's return is written onto the step it belongs to (``AgentStepOutput``),
and a failed one also marks that step. A subagent's return is its conclusion,
which otherwise reaches only the thread that spawned it, so it becomes an event
of its own instead.
"""

import json
import re
from collections.abc import Mapping, MutableMapping
from datetime import datetime

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.service import (
    STEP_ERROR_MAX,
    AgentCompacting,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentSessionInfo,
    AgentStepFailed,
    AgentStepOutput,
    AgentToolResult,
    AgentToolUse,
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


def bind(record: dict, facts: Mapping[str, str]) -> dict[str, str]:
    """The facts this record establishes, given the ones already known.

    ``facts`` maps ``call:<call id>`` to the call's name and description, and
    ``error`` to the API's refusal of the turn, when it refused one.
    """
    learned: dict[str, str] = {}
    message = _message(record)
    kind = message.get("type")
    if record.get("type") == "result":
        learned["error"] = ""
    elif kind == "assistant" and message.get("error"):
        # The API refused the turn; the result that follows says only that it
        # failed, and this is the one record that says how.
        learned["error"] = str(message["error"])
    if kind == "assistant":
        for block in (message.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            call = str(block.get("id"))
            name = str(block.get("name") or "")
            given = block.get("input")
            arguments: dict = given if isinstance(given, dict) else {}
            learned[f"call:{call}"] = json.dumps(
                {"name": name, "description": str(arguments.get("description") or "")},
                ensure_ascii=False,
            )
    return learned


class Assembler:
    """Records in, room events out."""

    def __init__(self, facts: MutableMapping[str, str], session_id: str | None = None):
        self.facts = facts
        self.session_id = session_id

    def _call(self, call: str) -> dict:
        try:
            return json.loads(self.facts.get(f"call:{call}") or "{}")
        except ValueError:
            return {}

    def accept(self, record: dict) -> list[AgentEvent]:
        kind = record.get("type")
        if kind == "result":
            # Before the facts: the result is read with what the turn left.
            ended = self._result(record)
            self.facts.update(bind(record, self.facts))
            return [ended]
        # Facts first: a record may name a call it also makes.
        self.facts.update(bind(record, self.facts))
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
            return _authored(self._returned(message), record, self.session_id)
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

    def _returned(self, message: dict) -> list[AgentEvent]:
        content = (message.get("message") or {}).get("content")
        events: list[AgentEvent] = []
        for index, block in enumerate(content if isinstance(content, list) else []):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call = str(block.get("tool_use_id"))
            said = _text(block.get("content"))
            made = self._call(call)
            if block.get("is_error"):
                if INTERRUPTED in said:
                    continue
                events.append(
                    AgentStepFailed(
                        call_id=call,
                        text=" ".join(EXIT_HEADER.sub("", said).split())[
                            -STEP_ERROR_MAX:
                        ],
                    )
                )
            if made.get("name") not in SUBAGENT_TOOLS or block.get("is_error"):
                if said.strip():
                    events.append(AgentStepOutput(call_id=call, text=said))
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

    def _result(self, record: dict) -> AgentResult:
        stamp = record.get("cheese") or {}
        common = {
            "session_id": str(record.get("session_id") or "") or self.session_id,
            "agent_handle": stamp.get("agent_handle"),
            "harness": CLAUDE_CODE,
        }
        if stamp.get("interrupted"):
            # Somebody took the work away. That ends the turn; it is not a
            # failure of anything, and saying 「出错了」 would tell the room so.
            return AgentResult(text="", **common)
        if not record.get("is_error"):
            return AgentResult(
                text=str(record.get("result") or ""),
                input_work_completed=True,
                **common,
            )
        # Failure is `is_error` and only that: an API error arrives with
        # subtype "success" and terminal_reason "api_error". The kind rides in
        # the text, where the room's classifier reads it (`billing` is how a
        # spent balance is told from a bad key).
        reason = self.facts.get("error") or str(
            record.get("terminal_reason") or record.get("subtype") or ""
        )
        errors = [str(e) for e in record.get("errors") or [] if str(e)] or [reason]
        said = str(record.get("result") or "").strip()
        status = record.get("api_error_status")
        return AgentResult(
            text=f"{said}（{reason}）" if said else f"AI 服务拒绝了请求（{reason}）",
            is_error=True,
            errors=errors,
            api_error_status=status if isinstance(status, int) else None,
            **common,
        )
