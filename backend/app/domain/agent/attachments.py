"""The attachment registry: which subscription instance owns a seat's events.

An event's source is not a session id, a token, or a mirror generation —
it is the SUBSCRIPTION instance that produced it (FB-56). A replaced
subscription keeps its identity with it, so an event draining from it can
be refused at the mutation boundary rather than discovered after the fact.

The registry is a module singleton because the protocol's two sides live in
one process: the driven runtimes swap subscriptions on one side, and the
hook consumer mutates the owner table on the other. The lock is the whole
of the ordering: a mutation holds it from validation through commit, and a
detach/attach takes it to swap, so every mutation is either whole-before or
whole-after every detach — never interleaved.
"""

import asyncio

_locks: dict[tuple, asyncio.Lock] = {}
_current: dict[tuple, str] = {}


def lock(seat: tuple) -> asyncio.Lock:
    """The per-seat lock every ownership mutation and every detach/attach
    takes."""
    if seat not in _locks:
        _locks[seat] = asyncio.Lock()
    return _locks[seat]


def note(seat: tuple, attachment_id: str | None) -> None:
    """The seat's subscription changed (called under :func:`lock`)."""
    if attachment_id is None:
        _current.pop(seat, None)
    else:
        _current[seat] = attachment_id


def current(seat: tuple) -> str | None:
    """The attachment id of the seat's live subscription, if any."""
    return _current.get(seat)
