"""The frozen first-match pairs (v4): structured exact identities, each citing the
concrete registration records it names — path and handler on both sides — plus
the witness URL that proved the behaviour. Nothing here is a blanket: a pair not
listed field-for-field is not exempt, and a listed pair that stops being true
breaks the guard on purpose (the ratchet shrinks only when the routes change).

Named by path and handler, never by registration position. The leaves below used
to be pinned by absolute index (727/751), so every route added above them moved
the pin and every PR that added a route had to touch this file and the unit test
asserting the numbers. Position is not what makes the pair; the two registrations
and the order between them are.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrozenPair:
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
        protocol="http",
        method="GET",
        earlier_path="/users/{userId}",
        earlier_endpoint="app.api.routes.users.account.get_user",
        later_path="/users/invite-codes",
        later_endpoint="app.api.routes.users.invite_codes.list_invite_codes",
        witness="/users/invite-codes",
        why=(
            "Documented known debt (users/invite_codes.py): invite-codes registered "
            "after {userId} and lands on its int parse. Frozen by the two "
            "registrations it names; the fix is a reorder in its own slice, not "
            "this guard's."
        ),
    ),
)
