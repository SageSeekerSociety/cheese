"""Turn Claude Code's stream-json records into the room's event vocabulary.

The records are what the runner journaled: every line Claude Code wrote to
stdout, plus the transcript lines of agents only their own file reports on
(``cheese_file``). One assistant record may carry several content blocks, so the
events a record comes out as each get their own id: the record's ``uuid``
(Claude Code's, stable across a re-read) plus the block's index.

Which card a sub-thread's work lands on is the contract's ``thread_label``
(结论 43), and no field of a Claude Code record carries one. The agent writes it
into the prompt it gives the subagent, so it is read there, when the spawning
call is seen, and bound to what later names that sub-thread: the call's own id
(``parent_tool_use_id`` on the subagent's stdout records) and the agent id the
build mints for it (``task_started``, and every line of its transcript file). An
agent a subagent starts without a label of its own is working for the same
card. Those bindings are ``facts``: worked out once, in journal order, when a
page is mirrored (``bind``), and read back by every later pass.

A tool's return is written onto the step it belongs to (``AgentStepOutput``),
and a failed one also marks that step. A subagent's return is its conclusion,
which otherwise reaches only the thread that spawned it, so it becomes an event
of its own instead.
"""

import json
import re
from datetime import datetime

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.service import (
    STEP_ERROR_MAX,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentSessionInfo,
    AgentStepFailed,
    AgentStepOutput,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
    AgentToolUse,
)
from app.domain.room_task.thread_label import label_in_text

#: The tools that start a sub-thread. `Task` is the older build's name for
#: `Agent`; a workflow starts several.
SPAWNING = {"Agent", "Task", "Workflow"}
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


def _retrying(record: dict, label: str | None) -> AgentRetrying:
    """``system/api_retry``, as the pinned build writes it: ``attempt``,
    ``max_retries``, ``retry_delay_ms``, ``error_status`` (null for a
    connection error) and ``error``, the kind of failure."""
    return AgentRetrying(
        error=str(record.get("error") or ""),
        attempt=_count(record.get("attempt")),
        max_attempts=_count(record.get("max_retries")),
        delay_ms=_count(record.get("retry_delay_ms")),
        status=_count(record.get("error_status")),
        thread_label=label,
    )


def _message(record: dict) -> tuple[dict, str | None]:
    """The transcript-shaped part of a record, and which thread it came from.

    The thread is named the way the facts are keyed: ``call:<id>`` for a
    subagent whose records are on stdout, ``agent:<id>`` for one read from its
    own file, None for the session's own thread.
    """
    if record.get("type") == "cheese_file":
        return record.get("entry") or {}, f"agent:{record.get('agent_id')}"
    parent = record.get("parent_tool_use_id")
    return record, f"call:{parent}" if parent else None


def bind(record: dict, facts: dict[str, str]) -> dict[str, str]:
    """The facts this record establishes, given the ones already known.

    ``facts`` maps ``spawned:<call id>`` and ``agent:<agent id>`` to the label of
    the card that sub-thread works on ("" for none), ``call:<call id>`` to the
    call's name and description, and ``task:<task id>`` to what kind of task the
    build started.
    """
    learned: dict[str, str] = {}

    def label_of(thread: str | None) -> str:
        if thread is None:
            return ""
        kind, _, key = thread.partition(":")
        name = f"spawned:{key}" if kind == "call" else f"agent:{key}"
        return learned.get(name, facts.get(name, ""))

    message, thread = _message(record)
    kind = message.get("type")
    if record.get("type") == "result":
        learned["error"] = ""
    elif kind == "assistant" and message.get("error"):
        # The API refused the turn; the result that follows says only that it
        # failed, and this is the one record that says how.
        learned["error"] = str(message["error"])
    if kind == "assistant":
        owner = label_of(thread)
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
            if name in SPAWNING:
                written = arguments.get("prompt") or arguments.get("script")
                learned[f"spawned:{call}"] = (
                    label_in_text(written if isinstance(written, str) else None)
                    or owner
                )
    elif kind == "system" and record.get("subtype") == "task_started":
        learned[f"task:{record.get('task_id')}"] = str(record.get("task_type") or "")
        if record.get("task_type") in ("local_agent", "local_workflow"):
            call = str(record.get("tool_use_id"))
            written = record.get("prompt")
            learned[f"agent:{record.get('task_id')}"] = label_in_text(
                written if isinstance(written, str) else None
            ) or learned.get(f"spawned:{call}", facts.get(f"spawned:{call}", ""))
    elif kind == "system" and record.get("subtype") == "task_progress":
        workflow = learned.get(
            f"agent:{record.get('task_id')}",
            facts.get(f"agent:{record.get('task_id')}", ""),
        )
        for entry in record.get("workflow_progress") or []:
            if isinstance(entry, dict) and entry.get("agentId"):
                learned.setdefault(f"agent:{entry['agentId']}", workflow)
    return learned


class Assembler:
    """Records in, room events out."""

    def __init__(self, facts: dict[str, str], session_id: str | None = None):
        self.facts = facts
        self.session_id = session_id

    def _label(self, thread: str | None) -> str | None:
        if thread is None:
            return None
        kind, _, key = thread.partition(":")
        name = f"spawned:{key}" if kind == "call" else f"agent:{key}"
        return self.facts.get(name) or None

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
        message, thread = _message(record)
        if message.get("type") == "assistant":
            return self._assistant(message, thread)
        if message.get("type") == "user" and not message.get("isReplay"):
            return self._returned(message, thread)
        return []

    def _assistant(self, message: dict, thread: str | None) -> list[AgentEvent]:
        if message.get("error"):
            # The build's own line for an API error. The turn's result reports
            # the failure; this would be the same news as a message from 芝士.
            return []
        label = self._label(thread)
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
                        thread_label=label,
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
                        thread_label=label,
                    )
                )
        return events

    def _returned(self, message: dict, thread: str | None) -> list[AgentEvent]:
        label = self._label(thread)
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
                        thread_label=label,
                    )
                )
            if made.get("name") not in SUBAGENT_TOOLS or block.get("is_error"):
                if said.strip():
                    events.append(
                        AgentStepOutput(call_id=call, text=said, thread_label=label)
                    )
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
                        thread_label=label,
                    )
                )
        return events

    def _system(self, record: dict) -> list[AgentEvent]:
        subtype = record.get("subtype")
        if subtype == "api_retry":
            return [_retrying(record, self._label(_message(record)[1]))]
        task = str(record.get("task_id") or "")
        # Only an agent is a worker the room tracks: a background command or a
        # workflow reports its tasks here too.
        if not task or self.facts.get(f"task:{task}") != "local_agent":
            return []
        label = self.facts.get(f"agent:{task}", "")
        if subtype == "task_started":
            return [
                AgentSubagentStart(
                    agent_id=task, thread_label=label, session_id=self.session_id
                )
            ]
        if subtype == "task_notification":
            path = record.get("output_file")
            return [
                AgentSubagentStop(
                    agent_id=task,
                    text=str(record.get("summary") or ""),
                    thread_label=label,
                    transcript_path=str(path) if path else None,
                    session_id=self.session_id,
                )
            ]
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
            return AgentResult(text=str(record.get("result") or ""), **common)
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
