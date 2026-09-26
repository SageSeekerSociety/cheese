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
"""

import re
from datetime import UTC, datetime

from app.domain.agent.harness.pi.journal import GAVE_UP, RETRYING
from app.domain.agent.service import (
    STEP_ERROR_MAX,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentStepFailed,
    AgentStepOutput,
    AgentToolUse,
    AgentUsage,
)

# pi stops for a tool call and keeps going; every other reason ends the turn,
# except a failed call, which ends it only once pi gives up on it (``GAVE_UP``).
CONTINUES = "toolUse"
FAILED = "error"
#: The HTTP status pi puts at the head of a provider error ("503: {...}").
STATUS = re.compile(r"\A(\d{3})\b")


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

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id
        self.spent = AgentUsage()

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

    def absorb(self, entry: dict) -> None:
        """Count an entry towards the turn without reporting it again.

        A pass that landed half a turn leaves the rest for the next one, which
        starts with an empty total. Replaying the part already landed is how the
        total resumes — the events are not re-emitted, only the arithmetic.
        """
        message = entry.get("message") or {}
        if entry.get("type") != "message":
            return
        if message.get("role") == "user":
            self.spent = AgentUsage()
        elif message.get("role") == "assistant":
            self._accumulate(message)

    def accept(self, entry: dict) -> list[AgentEvent]:
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
        if entry.get("type") != "message":
            return []
        message = entry.get("message") or {}
        role = message.get("role")
        if role == "user":
            # The room already holds what the person said; a turn starts here,
            # so this is where the running total goes back to zero.
            self.spent = AgentUsage()
            return []
        if role == "toolResult":
            # What the tool handed back goes onto its step, not onto a line of
            # its own. A FAILURE also marks that step, because the effect of a
            # failed step is that nothing happened, which looks exactly like a
            # step that is still going.
            call = message.get("toolCallId")
            if not isinstance(call, str):
                return []
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
                    harness="pi",
                )
            )
        return events

    def _gave_up(self, entry: dict) -> AgentResult:
        """The turn ends on the failed call pi stopped retrying — in failure,
        unless the platform itself asked pi to stop."""
        said = str(entry.get("errorMessage") or "")
        if entry.get("aborted"):
            return AgentResult(
                text="", session_id=self.session_id, usage=self.spent, harness="pi"
            )
        status = STATUS.match(said)
        return AgentResult(
            text=said or "AI 服务请求失败",
            session_id=self.session_id,
            usage=self.spent,
            is_error=True,
            errors=[said] if said else None,
            api_error_status=int(status[1]) if status else None,
            harness="pi",
        )
