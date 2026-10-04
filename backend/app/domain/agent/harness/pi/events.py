"""Turn pi's session entries into the room's event vocabulary.

The source is deliberately the ENTRY log (``get_entries``), not the live event
stream. Both describe the same turn, and only one of them can be read again: an
entry has a stable id and a parent, so a reader that died mid-turn resumes from
a cursor, while the live stream is gone the moment nobody is listening. The
live stream's job here is liveness and partial text, never the record.

One entry is not one thing. An assistant entry carries its thinking, whatever it
said, and every tool it called, all in one ``content`` list — so the events a
room shows come out of it several at a time, and each needs its own id. The
entry id plus the content index is that id: stable across a re-read, which is
what makes landing the same entry twice harmless.

Thinking is dropped rather than shown. It is not a message the agent addressed
to the room, and the room's timeline is what people read — the same call the
Claude Code path already makes, where hooks never deliver it either.

A tool's return is written onto the step it belongs to (``AgentStepOutput``;
the room keeps a capped tail of it), and a failed one also marks that step.

A model call that failed is the one thing the entry log cannot finish saying.
pi writes it as an assistant entry that stopped on ``error`` and only then
decides, on its live stream, whether to try again. So that entry ends nothing
here: the runner writes what pi decided into the same log right behind it
(``journal.RETRYING`` / ``journal.GAVE_UP``), and the turn ends on the second.

A subagent's entries are in the same log (``subagents.py``), each carrying its
thread (``journal.THREAD``): what it says and calls comes out labelled with the
card its work lands on, and nothing it does starts or ends the session's turn
or counts towards it. That it started and how it ended are the runner's
records. What a ``Task`` call hands back is the conclusion, and becomes an
event of its own rather than a line on the step (``AgentToolResult``).
"""

import re
from datetime import UTC, datetime
from typing import cast

from app.domain.agent.harness.pi.journal import (
    COMPACTING,
    GAVE_UP,
    RETRYING,
    SUBAGENT_STARTED,
    SUBAGENT_STOPPED,
    THREAD,
)
from app.domain.agent.service import (
    STEP_ERROR_MAX,
    AgentCompacting,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentStepFailed,
    AgentStepOutput,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
    AgentToolUse,
    AgentUsage,
    AgentUserEntry,
)

# pi stops for a tool call and keeps going; every other reason ends the turn,
# except a failed call, which ends it only once pi gives up on it (``GAVE_UP``).
CONTINUES = "toolUse"
FAILED = "error"
#: The HTTP status pi puts at the head of a provider error ("503: {...}").
STATUS = re.compile(r"\A(\d{3})\b")
#: The extension's tool that starts a subagent (`platform.ts`).
SPAWNING = "Task"


def thread_of(entry: dict) -> dict | None:
    """Which subagent wrote this entry, or None for the session's own."""
    thread = entry.get(THREAD)
    return thread if isinstance(thread, dict) else None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _said(content: object) -> str:
    """一次工具返回里的文字。pi 的 content 是内容块的列表（文本、图片…）。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


def _stamp(entry: dict) -> datetime | None:
    at = entry.get("message", {}).get("timestamp")
    return datetime.fromtimestamp(at / 1000, UTC) if at else None


class Assembler:
    """Entries in, room events out, with the turn's usage accumulated.

    Usage is summed across the turn's assistant entries rather than read off the
    last one: pi reports per message, and a turn that called three tools has
    three messages whose tokens were all spent on this turn. Reading only the
    final entry would bill a fraction and look plausible.
    """

    def __init__(
        self,
        session_id: str | None = None,
        *,
        harness: str,
    ):
        self.session_id = session_id
        self.harness = harness
        self.spent = AgentUsage()
        self.generation = ""
        self._positions: dict[str, int] = {}

    def _accumulate(self, message: dict) -> None:
        usage = message.get("usage") or {}
        self.spent = AgentUsage(
            model=message.get("responseModel") or message.get("model") or "",
            input_tokens=self.spent.input_tokens
            + int(usage.get("input", 0))
            + int(usage.get("cacheRead", 0))
            + int(usage.get("cacheWrite", 0)),
            output_tokens=self.spent.output_tokens + int(usage.get("output", 0)),
            cost_usd=self.spent.cost_usd
            + float((usage.get("cost") or {}).get("total", 0.0)),
        )

    def _pos_of(self, entry_id: str) -> int:
        """The entry's mirror position, or 0 when it is not ours to know."""
        return self._positions.get(entry_id, 0)

    def absorb(self, entry: dict) -> None:
        """Count an entry towards the turn without reporting it again.

        A pass that landed half a turn leaves the rest for the next one, which
        starts with an empty total. Replaying the part already landed is how the
        total resumes — the events are not re-emitted, only the arithmetic.
        """
        message = entry.get("message") or {}
        if entry.get("type") != "message" or thread_of(entry):
            return
        if message.get("role") == "user":
            self.spent = AgentUsage()
        elif message.get("role") == "assistant":
            self._accumulate(message)

    def accept(self, entry: dict) -> list[AgentEvent]:
        thread = thread_of(entry)
        if thread is not None:
            return self._subagent(entry, thread)
        if entry.get("type") == RETRYING:
            return [
                AgentRetrying(
                    error=str(entry.get("errorMessage") or ""),
                    attempt=_count(entry.get("attempt")),
                    max_attempts=_count(entry.get("maxAttempts")),
                    delay_ms=_count(entry.get("delayMs")),
                )
            ]
        if entry.get("type") == GAVE_UP:
            return [self._gave_up(entry)]
        if entry.get("type") == COMPACTING:
            if not entry.get("done"):
                return [AgentCompacting()]
            error = str(entry.get("errorMessage") or "")
            if entry.get("aborted"):
                error = error or "aborted"
            return [AgentCompacting(done=True, error=error)]
        if entry.get("type") != "message":
            return []
        message = entry.get("message") or {}
        role = message.get("role")
        if role == "user":
            # The room already holds what the person said; a turn starts here,
            # so this is where the running total goes back to zero. The entry
            # itself goes out as the binding point an input is matched to
            # (FB-56) — text, the mirror's own id and position, and the
            # mirror's generation, so the platform never trusts page order.
            self.spent = AgentUsage()
            content = message.get("content") or []
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            entry_id = str(entry.get("id") or "")
            user_entry = cast(
                list[AgentEvent],
                [
                    AgentUserEntry(
                        text,
                        entry_id=entry_id,
                        pos=self._pos_of(entry_id),
                        generation=self.generation,
                        session_id=self.session_id,
                        harness=self.harness,
                        eid=f"pi:user:{entry_id}",
                    )
                ],
            )
            return user_entry
        if role == "toolResult":
            # What the tool handed back goes onto its step, not onto a line of
            # its own. A FAILURE also marks that step, because the effect of a
            # failed step is that nothing happened, which looks exactly like a
            # step that is still going.
            call = message.get("toolCallId")
            if not isinstance(call, str):
                return []
            details = message.get("details")
            if (
                message.get("toolName") == SPAWNING
                and isinstance(details, dict)
                and "report" in details
                and not message.get("isError")
            ):
                # A subagent's conclusion, which otherwise reaches only this
                # session's context. Only a call that waited for one carries it.
                return [
                    AgentToolResult(
                        name=SPAWNING,
                        text=str(details.get("report") or "").strip(),
                        description=str(details.get("description") or ""),
                        eid=f"pi:{entry['id']}",
                    )
                ]
            returned = _said(message.get("content"))
            steps: list[AgentEvent] = []
            if message.get("isError"):
                text = " ".join(returned.split())
                steps.append(AgentStepFailed(call_id=call, text=text[-STEP_ERROR_MAX:]))
            if returned.strip():
                steps.append(AgentStepOutput(call_id=call, text=returned))
            return steps
        if role != "assistant":
            return []
        self._accumulate(message)
        if message.get("stopReason") == FAILED:
            # Whatever it had streamed before failing is not what it said: a
            # retry asks again from the same point and says it anew.
            return []
        events: list[AgentEvent] = []
        said: list[str] = []
        for index, part in enumerate(message.get("content") or []):
            eid = f"pi:{entry['id']}:{index}"
            if part.get("type") == "text" and part.get("text"):
                said.append(part["text"])
                events.append(
                    AgentMessage(
                        part["text"],
                        session_id=self.session_id,
                        eid=eid,
                        eids=(eid,),
                        at=_stamp(entry),
                    )
                )
            elif part.get("type") == "toolCall":
                events.append(
                    AgentToolUse(
                        part.get("name", ""),
                        part.get("arguments") or {},
                        session_id=self.session_id,
                        eid=eid,
                        call_id=part.get("id"),
                    )
                )
        if message.get("stopReason") != CONTINUES:
            events.append(
                AgentResult(
                    text=said[-1] if said else "",
                    session_id=self.session_id,
                    usage=self.spent,
                    harness=self.harness,
                )
            )
        return events

    def _subagent(self, entry: dict, thread: dict) -> list[AgentEvent]:
        """One record of a subagent's thread: what it said and called, on its
        card, and never anything that starts, ends or bills the session's turn."""
        agent = str(thread.get("id") or "")
        label = str(thread.get("label") or "")
        if entry.get("type") == SUBAGENT_STARTED:
            return [
                AgentSubagentStart(
                    agent_id=agent, thread_label=label, session_id=self.session_id
                )
            ]
        if entry.get("type") == SUBAGENT_STOPPED:
            return [
                AgentSubagentStop(
                    agent_id=agent,
                    text=str(entry.get("text") or ""),
                    thread_label=label,
                    session_id=self.session_id,
                )
            ]
        if entry.get("type") != "message":
            return []
        message = entry.get("message") or {}
        role = message.get("role")
        on = label or None
        if role == "toolResult":
            call = message.get("toolCallId")
            if not isinstance(call, str):
                return []
            returned = _said(message.get("content"))
            steps: list[AgentEvent] = []
            if message.get("isError"):
                text = " ".join(returned.split())
                steps.append(
                    AgentStepFailed(
                        call_id=call, text=text[-STEP_ERROR_MAX:], thread_label=on
                    )
                )
            if returned.strip():
                steps.append(
                    AgentStepOutput(call_id=call, text=returned, thread_label=on)
                )
            return steps
        if role != "assistant" or message.get("stopReason") == FAILED:
            return []
        events: list[AgentEvent] = []
        for index, part in enumerate(message.get("content") or []):
            eid = f"pi:{entry['id']}:{index}"
            if part.get("type") == "text" and part.get("text"):
                events.append(
                    AgentMessage(
                        part["text"],
                        eid=eid,
                        eids=(eid,),
                        at=_stamp(entry),
                        thread_label=on,
                    )
                )
            elif part.get("type") == "toolCall":
                events.append(
                    AgentToolUse(
                        part.get("name", ""),
                        part.get("arguments") or {},
                        session_id=self.session_id,
                        eid=eid,
                        call_id=part.get("id"),
                        thread_label=on,
                    )
                )
        return events

    def _gave_up(self, entry: dict) -> AgentResult:
        """The turn ends on the failed call pi stopped retrying — in failure,
        unless the platform itself asked pi to stop."""
        said = str(entry.get("errorMessage") or "")
        if entry.get("aborted"):
            return AgentResult(
                text="",
                session_id=self.session_id,
                usage=self.spent,
                harness=self.harness,
            )
        status = STATUS.match(said)
        return AgentResult(
            text=said or "AI 服务请求失败",
            session_id=self.session_id,
            usage=self.spent,
            is_error=True,
            errors=[said] if said else None,
            api_error_status=int(status[1]) if status else None,
            harness=self.harness,
        )
