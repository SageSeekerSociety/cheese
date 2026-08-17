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


def parse_work_id(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
