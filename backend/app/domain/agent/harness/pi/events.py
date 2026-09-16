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

Tool returns are dropped too, with one exception: a failed one marks the step it
belongs to. Everything else a tool returns is already visible through its effect,
and a read's return is the whole file.
"""

from datetime import UTC, datetime

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentStepFailed,
    AgentToolUse,
    AgentUsage,
)

# pi stops for a tool call and keeps going; every other reason ends the turn.
CONTINUES = "toolUse"

#: 一条失败摘要在现场占多少 —— 和 Claude Code 那一侧同一个数。
STEP_ERROR_MAX = 500


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
            # A tool's return value does not become an event: a read's return is
            # the whole file, and the room is for people to read. A FAILURE is
            # the exception, because it is the one thing the room cannot learn
            # from the effect — the effect of a failed step is that nothing
            # happened, which looks exactly like a step that is still going.
            # It marks the step already on the timeline rather than adding one.
            if not message.get("isError"):
                return []
            call = message.get("toolCallId")
            if not isinstance(call, str):
                return []
            text = " ".join(_said(message.get("content")).split())
            return [AgentStepFailed(call_id=call, text=text[-STEP_ERROR_MAX:])]
        if role != "assistant":
            return []
        self._accumulate(message)
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
                        complete_identity=True,
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
