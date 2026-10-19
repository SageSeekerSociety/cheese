"""A channel's pins as its overview reads them.

`pin_rows` builds them once: `GET /topics/{id}/pins` returns the rows, and the
`pins` of the room snapshot a subscription opens with (`app.api.room_snapshot`)
embeds the same ones, so the pins drawn from the snapshot and the ones the route
answers with are never two different readings of the same board.

It sits under `app.api` rather than beside its route (`routes/topics_pins.py`)
because the snapshot is not a route and must not become one that reaches a
route — C4 (`routes-independent`).
"""

import uuid

from app.domain.agent_instance.adapters import project_team
from app.domain.block.adapters import dump_blocks_json
from app.domain.pin.services import Pins


async def pin_rows(db, room_id: uuid.UUID) -> list[dict]:
    """A channel's pins as `GET /topics/{id}/pins` lists them; also the `pins`
    of the room snapshot a subscription opens with. The caller has authorised
    the reader."""
    # The blocks go through one batched dump rather than a per-pin
    # `BlockOut.model_validate`, which validates every row on the event loop
    # without a chance to yield. `dump_blocks_json` chunks the work (500 rows)
    # and yields between chunks; `of_room` already ordered the pairs, and the
    # dump keeps that order, so `pinned_at` newest-first is unchanged.
    pairs = await Pins(db, team=project_team).of_room(room_id)
    dumped = await dump_blocks_json([block for _, block in pairs])
    return [
        {
            "pinned_by": pin.pinned_by,
            "pinned_at": pin.pinned_at.isoformat(),
            "block": block,
        }
        for (pin, _), block in zip(pairs, dumped, strict=True)
    ]
