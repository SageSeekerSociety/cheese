"""Ambient work attribution for cheese-side block writes.

Conversation blocks carry their ``turn_id`` explicitly. Blocks created later by
cheese REST handlers run in separate requests, so the CLI repeats that same work
id in ``X-Cheese-Turn`` and middleware stores it here. The legacy wire and column
names remain unchanged; the value is an originating message or platform work id.
"""

import uuid
from contextvars import ContextVar

current_work_id: ContextVar[uuid.UUID | None] = ContextVar(
    "current_work_id", default=None
)

# Which thread the current request is speaking in, when it is speaking in one.
#
# Same problem as `current_work_id`, one level over: a `cheese` command run
# inside a piece of work reaches the backend through a REST handler whose URL
# names a place, and every block that handler writes belongs to that place's
# thread. Without this the handler would write to the room's main line, which
# does not fail — it just puts the thread's messages where everyone can see them
# and the thread cannot.
#
# None means the room's own main line, which is also the honest answer for every
# request that never named a thread.
current_thread_id: ContextVar[uuid.UUID | None] = ContextVar(
    "current_thread_id", default=None
)


def parse_work_id(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
