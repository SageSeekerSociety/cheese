"""Where a turn happens: a room, and the tree its work is written on.

Everything that runs — a prompt, a workspace, an accept card, a spend record —
is addressed by the id of a `topics` row, because a turn happens in a room and
nowhere else. A piece of work is not a place: it is a CARD in a room, done by a
分身 inside that room's one session, and the room is where its conversation is
read and where its turns are opened.

The tree rides along because almost every caller that has a place goes on to
want files, and "which branch am I writing to" is not a property of the room —
it is whichever tree the room is currently taking work into, and a sealed room
has none.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.models import TreeStatus, WorkTree
from app.domain.topic.models import Topic


@dataclass(frozen=True)
class Place:
    """A room, and the tree it is currently taking work into."""

    room: Topic
    #: 这个地点的活写在哪棵树上 — whichever tree the room is taking work into.
    #: None means there is no writable tree right now: the room is sealed,
    #: waiting on the PR its last tree opened. Callers that need files must say
    #: what they do about that rather than silently writing into the PR under
    #: review.
    tree: WorkTree | None = None

    @property
    def room_id(self) -> uuid.UUID:
        return self.room.id

    @property
    def project_id(self) -> uuid.UUID:
        return self.room.project_id

    @property
    def title(self) -> str:
        return self.room.title

    @property
    def tree_id(self) -> uuid.UUID | None:
        return self.tree.id if self.tree is not None else None

    @property
    def branch_name(self) -> str | None:
        """The branch this place's work is on — its TREE's.

        一棵树 = 一个分支 = 一个 PR = 一批活, so every card the room is working
        on right now reports the same branch. That is not a rounding error:
        they are genuinely writing to one place, and saying otherwise would
        promise an isolation that does not exist.

        None when the room is sealed and has not started its next batch — there
        is no branch to name because there is nothing writable.
        """
        from app.domain.workspace.service import branch_for_tree

        return branch_for_tree(self.tree.id) if self.tree is not None else None


class PlaceResolver:
    """Turns one id back into the room it names."""

    def __init__(self, session: AsyncSession):
        # Primary-key `session.get` rather than the topic domain's repository:
        # resolving a place is not a query anyone gets to shape, and reaching
        # into `topic.repositories` from here would be exactly the cross-domain
        # coupling `test_domain_import_guard` exists to stop.
        self._session = session

    async def resolve(self, place_id: uuid.UUID) -> Place | None:
        """The room *place_id* names, or None if it names nothing.

        A card's id is not an address: it names a row in `tasks`, which this
        deliberately does not look at. Handing one to a route that takes a
        place gets a 404, and that is the answer — the card is read through its
        room (`GET /topics/{room}/tasks/{card}`), where the person reading it
        already is.
        """
        topic = await self._session.get(Topic, place_id)
        if topic is None:
            return None
        return Place(room=topic, tree=await self._open_tree(topic.id))

    async def _open_tree(self, room_id: uuid.UUID) -> WorkTree | None:
        stmt = select(WorkTree).where(
            WorkTree.room_id == room_id, WorkTree.status == TreeStatus.open
        )
        return (await self._session.scalars(stmt)).first()
