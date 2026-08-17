"""What is left of the retired machine gate.

采纳即合并 (#296, stage 1) retired it: an accept card is a view of a PR, and the
real CI on that PR decides — not a private platform check. `create_card` no
longer mints a `pending_gate` card and `routes/accept.py` no longer dispatches a
runner, so the runner, the check-command execution and their tests are gone with
it. A guard test (`test_accept_gate.py`) pins that filing a card cannot reach
`pending_gate` again.

Two things survive, both for `review/gate_sweep.py`, which still has to clean up
`pending_gate` rows written before the retirement: a timeout to age those rows
against, and the question "is this card's gate still running in this process?".
The answer is now permanently no, and that is precisely what lets the sweep
condemn every stale row it finds instead of tiptoeing around live work.

`gate_failed` / `gate_blocked` remain in the card-status enum and in the
frontend. Nothing produces them any more, but historical rows carry them and
must still render.
"""

import uuid

# Kept as the age threshold gate_sweep measures stale rows against.
GATE_TIMEOUT_S = 600


def in_flight_card_ids() -> frozenset[uuid.UUID]:
    """Cards whose gate is still running in THIS process — now always none."""
    return frozenset()
