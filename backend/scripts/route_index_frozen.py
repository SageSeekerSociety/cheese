"""The frozen first-match pairs (v3): structured exact identities, each citing the
concrete registration records it names — protocol, method, path and handler on
both sides — plus the real-pair run that proved the behaviour. Nothing here is
a blanket: a pair not listed field-for-field is not exempt, and a listed pair
that stops being true breaks the guard on purpose (the ratchet shrinks
only when the routes change).

A pair holds no ordinal. Which of its two registrations comes first is a
property of the running app's registration order, so the guard reads it off the
collected sequence at check time; adding routes elsewhere no longer renumbers
anything here.
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
            "after {userId} and lands on its int parse. Frozen citing the "
            "registration pair by protocol/method/path/handler; the fix is a "
            "reorder in its own slice, not this guard's."
        ),
    ),
)
