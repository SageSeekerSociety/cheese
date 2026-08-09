"""Ambient turn id for cheese-side block writes (review R4).

The conversation blocks converse writes carry their turn_id explicitly. The blocks
created by cheese REST handlers (a decision, a doc edit event, a returned
conclusion…) run in a separate request, so we thread the turn id ambiently: the
`cheese` CLI sends X-Cheese-Turn, a middleware stashes it here, and
BlockRepository.add defaults to it — so every block a turn produces shares one
turn_id with no per-handler plumbing.
"""

import uuid
from contextvars import ContextVar

current_turn_id: ContextVar[uuid.UUID | None] = ContextVar(
    "current_turn_id", default=None
)


def parse_turn_id(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
