"""The frozen first-match pairs (v3): structured exact identities, each citing the
concrete registration records it names — leaf index and handler on both
sides — plus the real-pair run that proved the behaviour. Nothing here is a
blanket: a pair not listed field-for-field is not exempt, and a listed pair
that stops being true breaks the guard on purpose (the ratchet shrinks
only when the routes change).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrozenPair:
    earlier_index: int
    later_index: int
    protocol: str
    method: str
    earlier_path: str
    earlier_endpoint: str
    later_path: str
    later_endpoint: str
    witness: str
    why: str


#: No new exemption: the same documented pair, now machine-validated.
FROZEN: tuple[FrozenPair, ...] = (
    FrozenPair(
        earlier_index=642,
        later_index=666,
        protocol="http",
        method="GET",
        earlier_path="/users/{userId}",
        earlier_endpoint="app.api.routes.users.account.get_user",
        later_path="/users/invite-codes",
        later_endpoint="app.api.routes.users.invite_codes.list_invite_codes",
        witness="/users/invite-codes",
        why=(
            "Documented known debt (users/invite_codes.py): invite-codes registered "
            "after {userId} and lands on its int parse. Frozen citing "
            "registration records #642/#666; the fix is a reorder in its own "
            "slice, not this guard's."
        ),
    ),
)
