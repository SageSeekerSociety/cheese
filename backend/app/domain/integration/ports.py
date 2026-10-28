"""What ``integration`` asks the room for that only a higher layer answers.

Reading a file out of a room's file area is how a drafted mail gets its
attachments, and the room's tree belongs to ``app.domain.library`` — layer 4 in
``docs/manual/dev/architecture.md``, one above this package. So ``integration``
names the one call it needs, ``app.domain.library.service.read_room_file``
already answers it, and the composition root hands one to ``IntegrationService``.

Nothing here needs a session: the read is a path lookup on disk under the
project's room tree, the same one the room-files routes make.
"""

from typing import Protocol
from uuid import UUID


class TeammateNames(Protocol):
    """What a project's AI teammate is called, for a sentence a person reads."""

    async def name_of(self, project_id: UUID, seat: str) -> str:
        """The teammate seated as ``seat``: the name its project gave it, or
        the reader-language default when it still has the one it was born
        with. An error message is shown outside any room, so a ``<@seat>``
        token in it would reach the reader as written."""
        ...


class RoomFiles(Protocol):
    """Reading one file out of a room's file area."""

    def read_room_file(self, project_id: UUID, room_id: UUID, path: str) -> bytes:
        """The bytes of this file, or a ``ValidationError`` if the room has no
        file at that path."""
        ...
