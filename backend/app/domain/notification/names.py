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

from app.domain.user.repositories import UserRepository
from app.domain.user.services import names_by_handles, remarks_by_handles

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


_NAME_SUFFIX = "Name"


def with_snapshot_names(value: Any, names: Mapping[str, str]) -> Any:
    """Also relabel the ``<key>Name`` snapshot written beside a ``<key>`` handle.

    A written payload keeps a name as it was at the time (``authorName`` beside
    ``author``), because the email and the push are rendered without a database.
    On the page, though, the reader's own remark has to win over that snapshot —
    otherwise a notification about someone they renamed still shows the old
    label. Only a sibling string that is itself a known handle is swapped, so
    ``teamName`` (whose ``team`` is an entity reference, not a string) is left
    alone.
    """
    if isinstance(value, Mapping):
        renamed: dict[Any, Any] = {}
        for key, nested in value.items():
            if isinstance(key, str) and key.endswith(_NAME_SUFFIX):
                source = value.get(key[: -len(_NAME_SUFFIX)])
                if isinstance(source, str) and source in names:
                    renamed[key] = names[source]
                    continue
            renamed[key] = with_snapshot_names(nested, names)
        return renamed
    if isinstance(value, list):
        return [with_snapshot_names(nested, names) for nested in value]
    if isinstance(value, tuple):
        return tuple(with_snapshot_names(nested, names) for nested in value)
    return value


async def names_for(
    session: AsyncSession,
    *values: Any,
    viewer_id: int | None = None,
) -> dict[str, str]:
    """The names of everyone the tokens inside ``values`` name, in one query.

    ``viewer_id`` is the reader this text is being rendered for. A personal
    remark (``UserTeammateRemark``) is private to the person who set it, and a
    notification is shown to one recipient, so the reader's own remarks replace
    the public nickname here exactly as the roster does on a page. Absent, the
    text keeps the public name — the sharing, export and push-to-others paths
    that must not carry a private alias.
    """
    handles: set[str] = set()
    for value in values:
        handles |= handles_in(value)
    if not handles:
        return {}
    names = await names_by_handles(session, sorted(handles))
    viewer_handle = await handle_of_id(session, viewer_id)
    if not viewer_handle:
        return names
    remarks = await remarks_by_handles(session, viewer_handle, sorted(handles))
    names.update(remarks)
    return names


async def handle_of_id(session: AsyncSession, user_id: int | None) -> str | None:
    """The account handle behind a user id, or None when there is no such user."""
    if user_id is None:
        return None
    user = await UserRepository(session).get_by_id(user_id)
    return user.username if user is not None else None
