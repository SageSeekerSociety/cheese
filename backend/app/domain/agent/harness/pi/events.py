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
"""

from datetime import UTC, datetime

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentToolUse,
    AgentUsage,
)

# pi stops for a tool call and keeps going; every other reason ends the turn.
CONTINUES = "toolUse"


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
        if role != "assistant":
            # A tool's return value is visible in the room through its effect —
            # a file changed, a command's output scrolled past. pi has no
            # subagents, so there is no return that reaches nobody.
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
