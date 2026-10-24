"""People in what 芝士 reads, by the name its person calls them.

Messages carry handles: the author's, and ``<@handle>`` for each person they
mention. The person says names ("tell 李老师"), and 芝士 acts with handles, so
it reads both: 「李明（liming）」, and 「@林溪（linxi）」 for a mention.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.services import faces_by_handle

_MENTION = re.compile(r"<@([\w.-]+)>")


class Names:
    def __init__(self, names: dict[str, str]) -> None:
        self._names = names

    def of(self, handle: str) -> str:
        name = self._names.get(handle)
        return f"{name}（{handle}）" if name and name != handle else handle

    def said(self, text: str) -> str:
        """``text`` with each ``<@handle>`` written as 「@名字（handle）」."""
        return _MENTION.sub(lambda m: "@" + self.of(m.group(1)), text)


async def names(session: AsyncSession, texts: Iterable[tuple[str, str]]) -> Names:
    """The names of the authors and of everyone mentioned in ``texts``, given
    as (author, text) pairs: two queries however many there are."""
    handles: set[str] = set()
    for author, text in texts:
        handles.add(author)
        handles.update(_MENTION.findall(text))
    faces = await faces_by_handle(session, handles)
    return Names({h: name for h, (name, _) in faces.items() if name})
