"""A notification is read without the room around it, so it names people by name.

A room line names a person with a ``<@handle>`` token, which the room draws as a
chip with the name the roster gives that handle. A notification about that line
leaves the room: an inbox row, a browser push and an email draw no chips, and a
token there reads as ``<@rion_shirakawa>``. So every place a notification is
shown to someone swaps each token for the name that person goes by, read when
it is shown (a rename shows up on the next read), and keeps the stored payload
as it was written.
"""

import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.services import names_by_handles

_TOKEN = re.compile(r"<@([\w-]+)>")


def handles_in(value: Any) -> set[str]:
    """Every handle a ``<@handle>`` token names anywhere inside ``value``."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(_TOKEN.findall(value))
    elif isinstance(value, Mapping):
        for nested in value.values():
            found |= handles_in(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found |= handles_in(nested)
    return found


def with_names(value: Any, names: Mapping[str, str]) -> Any:
    """``value`` with every token replaced by the name in ``names``, or by the
    bare handle when nobody by that handle is known. A new structure; ``value``
    is left as it was."""
    if isinstance(value, str):
        return _TOKEN.sub(lambda m: names.get(m.group(1), m.group(1)), value)
    if isinstance(value, Mapping):
        return {key: with_names(nested, names) for key, nested in value.items()}
    if isinstance(value, list):
        return [with_names(nested, names) for nested in value]
    if isinstance(value, tuple):
        return tuple(with_names(nested, names) for nested in value)
    return value


async def names_for(session: AsyncSession, *values: Any) -> dict[str, str]:
    """The names of everyone the tokens inside ``values`` name, in one query."""
    handles: set[str] = set()
    for value in values:
        handles |= handles_in(value)
    return await names_by_handles(session, sorted(handles)) if handles else {}
