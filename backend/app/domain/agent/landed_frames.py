"""Which tool calls just came back, told to the conversation as their records land.

A ``live`` frame (`live_frames`) shows a tool call while its arguments are
still being written; nothing then says when the call has run. A reader
following the AI teammate's edits in the editor needs that moment: before it,
an edit is only proposed and the file on disk is unchanged; after it, the file
holds the edit (or the call failed and holds nothing new). So when a session's
journal rows are kept, the calls those rows answer go out as one ``landed``
frame on the conversation's channel: each call's id — the same id its ``live``
block carried — and whether it failed.

Like ``live``, it is not stored and not replayed. A client that missed it reads
the file again on its next look, which is what it would have done on this
frame. A row journaled twice may say the same call twice; a reader treats the
second as the first.
"""

from app.core.db import SessionFactory
from app.domain.agent.realtime.broker import get_broker
from app.domain.session_record import service as session_records
from app.domain.session_record.views import parts


def landed(rows: list[dict]) -> list[dict]:
    """The calls these journal rows answer, in order: id and whether it failed."""
    calls = []
    for row in rows:
        for part in parts(row["record"]):
            call = part.get("call_id")
            if part["kind"] == "output" and call:
                calls.append({"call_id": call, "failed": bool(part["failed"])})
    return calls


async def keep_and_tell(
    sessions: SessionFactory,
    stream: session_records.Stream,
    rows: list[dict],
) -> int:
    """Keep the rows (`session_record.service.append`), then say which calls
    they answer; how many rows were new."""
    kept = await session_records.append(sessions, stream, rows)
    if calls := landed(rows):
        await get_broker().publish(
            str(stream.conversation_id),
            {"type": "landed", "agent": stream.agent_handle, "calls": calls},
        )
    return kept
