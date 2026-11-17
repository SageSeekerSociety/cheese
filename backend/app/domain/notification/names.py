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
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.services import (
    names_by_handles,
    remarks_by_handles,
    usernames_by_ids,
)

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


_USER_ID = re.compile(r"^\d+$")


def user_ids_in(value: Any) -> set[int]:
    """Every user id a ``{"type": "user", "id": "<int>"}`` reference names.

    A payload may name a person by an entity reference instead of a
    ``<@handle>`` token (``requester`` in a join request). That reference is what
    says who the person is, and the snapshot beside it (``requesterName``) is
    what the page has to relabel.
    """
    found: set[int] = set()
    if isinstance(value, Mapping):
        if (
            value.get("type") == "user"
            and isinstance(value.get("id"), str)
            and _USER_ID.match(value["id"])
        ):
            found.add(int(value["id"]))
        for nested in value.values():
            found |= user_ids_in(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found |= user_ids_in(nested)
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


def snapshot_swaps(
    names: Mapping[str, str], public_names: Mapping[str, str]
) -> dict[str, str]:
    """Public name -> the remark that replaced it, for this one reader.

    A written payload keeps a name as it was at the time (``authorName`` beside
    ``author``), because the email and the push are rendered without a database.
    On the page the reader's own remark has to win over that snapshot instead.
    Where the snapshot sits beside the person's own handle, ``names`` relabels it
    directly; this map is for the case where the sibling is a user entity
    reference (``requesterName`` beside ``requester``), and the only way back to
    who it names is the public name itself. Names the reader has no remark for
    are absent.
    """
    return {
        public: names[handle]
        for handle, public in public_names.items()
        if handle in names and names[handle] != public
    }


def _names_a_user_reference(holder: Mapping[str, Any], key: str) -> bool:
    """Whether ``key``'s sibling (``<key>Name`` -> ``<key>``) is a user reference."""
    ref = holder.get(key[: -len(_NAME_SUFFIX)])
    return isinstance(ref, Mapping) and ref.get("type") == "user"


def with_snapshot_names(
    value: Any,
    names: Mapping[str, str],
    swaps: Mapping[str, str] | None = None,
) -> Any:
    """Also relabel the ``<key>Name`` snapshot written beside a person.

    ``names`` relabels a snapshot whose sibling is the person's own handle.
    ``swaps`` (``snapshot_swaps``) covers the case where the sibling is a user
    entity reference instead, and is applied **only** to such a sibling: display
    names are not unique across people, teams and agents, so matching the string
    alone would rename whoever happens to share it. Anything else — a team's
    name, say — is left alone.
    """
    swaps = swaps or {}
    if isinstance(value, Mapping):
        renamed: dict[Any, Any] = {}
        for key, nested in value.items():
            if isinstance(key, str) and key.endswith(_NAME_SUFFIX):
                snapshot = value.get(key)
                if isinstance(snapshot, str) and snapshot:
                    source = value.get(key[: -len(_NAME_SUFFIX)])
                    if isinstance(source, str) and source in names:
                        renamed[key] = names[source]
                        continue
                    if snapshot in swaps and _names_a_user_reference(value, key):
                        renamed[key] = swaps[snapshot]
                        continue
            renamed[key] = with_snapshot_names(nested, names, swaps)
        return renamed
    if isinstance(value, list):
        return [with_snapshot_names(nested, names, swaps) for nested in value]
    if isinstance(value, tuple):
        return tuple(with_snapshot_names(nested, names, swaps) for nested in value)
    return value


async def names_for(
    session: AsyncSession,
    *values: Any,
    viewer_id: int | None = None,
    viewer_handle: str | None = None,
) -> dict[str, str]:
    """The names of everyone the tokens inside ``values`` name, in one query.

    ``viewer_id`` (or ``viewer_handle``, when the caller already resolved it) is
    the reader this text is being rendered for. A personal
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
    return await names_for_reader(
        session,
        sorted(handles),
        await names_by_handles(session, sorted(handles)),
        viewer_id=viewer_id,
        viewer_handle=viewer_handle,
    )


async def names_for_reader(
    session: AsyncSession,
    handles: Sequence[str],
    public_names: Mapping[str, str],
    *,
    viewer_id: int | None = None,
    viewer_handle: str | None = None,
) -> dict[str, str]:
    """``public_names`` overlaid with the reader's own remarks.

    Split out so a caller rendering a batch for several recipients can read the
    public names once and then pay only one remark query per reader.
    """
    names = dict(public_names)
    if viewer_handle is None:
        viewer_handle = await handle_of_id(session, viewer_id)
    if not viewer_handle:
        return names
    remarks = await remarks_by_handles(session, viewer_handle, list(handles))
    names.update(remarks)
    return names


async def handle_of_id(session: AsyncSession, user_id: int | None) -> str | None:
    """The account handle behind a user id, or None when there is no such user."""
    if user_id is None:
        return None
    return (await usernames_by_ids(session, [user_id])).get(user_id)


async def handles_of_ids(
    session: AsyncSession, user_ids: Iterable[int]
) -> dict[int, str]:
    """user_id -> account handle, one query for however many recipients.

    A batch of notifications goes to several people at once, and each one reads
    their own remarks: resolving the reader id to a handle once per delivery
    turned a per-recipient lookup into a query per person.
    """
    return await usernames_by_ids(session, user_ids)
