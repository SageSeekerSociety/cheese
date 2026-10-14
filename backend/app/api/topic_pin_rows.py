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

from app.domain.block.schemas import BlockOut
from app.domain.pin.services import Pins


async def pin_rows(db, room_id: uuid.UUID) -> list[dict]:
    """A channel's pins as `GET /topics/{id}/pins` lists them; also the `pins`
    of the room snapshot a subscription opens with. The caller has authorised
    the reader."""
    return [
        {
            "pinned_by": pin.pinned_by,
            "pinned_at": pin.pinned_at.isoformat(),
            "block": BlockOut.model_validate(block).model_dump(mode="json"),
        }
        for pin, block in await Pins(db).of_room(room_id)
    ]
