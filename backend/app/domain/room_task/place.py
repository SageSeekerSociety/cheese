"""Where a turn happens: a room, plus which thread in it.

Everything that runs — a prompt, a workspace, an accept card, a spend record —
used to be addressed by one id, and that id was always a `topics` row, because
a piece of work WAS a room. It is not any more, so the address is a pair: the
room (which never ends, and is where a person reads) and the task (which does
end, and is what the work actually is). ``None`` for the task means the room's
own main line.

The pair is a single object rather than two parameters for one reason: the two
halves are wrong independently. A caller holding only the task cannot say where
to render anything; a caller holding only the room silently addresses the main
line, which is the bug this design is most exposed to — it does not raise, it
just puts a thread's message in the room.

One id still identifies a place, and `resolve` is what turns it back into one.
That works because a task keeps the id of the `topics` row it replaced
(migration `c4a7e91b2d05`), so nothing that already holds an id — a per-turn
token, a session key, a branch name, a webhook — had to be reissued.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.models import Task
from app.domain.room_task.repositories import TaskRepository
from app.domain.topic.models import Topic
from app.domain.topic.repositories import TopicRepository


@dataclass(frozen=True)
class Place:
    """A room, and optionally one thread inside it."""

    room: Topic
    task: Task | None = None

    @property
    def id(self) -> uuid.UUID:
        """The id this place is addressed by — the thread's when there is one.

        This is what a per-turn token carries, what a branch name derives from,
        and what `resolve` takes back. The room's own id when the turn is the
        room's own.
        """
        return self.task.id if self.task is not None else self.room.id

    @property
    def room_id(self) -> uuid.UUID:
        return self.room.id

    @property
    def task_id(self) -> uuid.UUID | None:
        """The thread key, and the exact value the `task_id` columns want:
        NULL on the room's main line, the task otherwise."""
        return self.task.id if self.task is not None else None

    @property
    def project_id(self) -> uuid.UUID:
        return self.room.project_id

    @property
    def title(self) -> str:
        """What this place is called — the WORK's title when it is a thread.

        A thread's title is the sentence the work was dispatched with, and the
        room's is a place name. Handing an agent the room's name as the subject
        of its turn is how a 分身 ends up believing its job is the room.
        """
        return self.task.title if self.task is not None else self.room.title

    @property
    def agent_instance_id(self) -> uuid.UUID | None:
        """Which agent works here — the thread's own pin, else the room's.

        A thread inherits the room's agent at dispatch, so this is normally the
        same answer twice; it stops being the same the moment a thread is handed
        to a different teammate.
        """
        if self.task is not None:
            return self.task.agent_instance_id
        return self.room.agent_instance_id

    @property
    def is_thread(self) -> bool:
        return self.task is not None


async def room_and_task(
    session: AsyncSession, place_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """The (room, thread) pair to STORE for a place named by one id.

    The whole application keeps addressing a place by a single id — that is what
    a per-turn token carries and what every route already has. Only the tables
    that must hold both halves pay for the lookup, and they pay it once per
    write, not once per row read.

    An id that names nothing comes back as (itself, None). That is what the
    foreign key would have said anyway, one statement later and with a message
    naming the actual column — better than raising here and turning a bad id
    into a different error than the one the schema gives it.
    """
    task = await session.get(Task, place_id)
    if task is None:
        return place_id, None
    return task.room_id, task.id


class PlaceResolver:
    """Turns one id back into the room + thread it names."""

    def __init__(self, session: AsyncSession):
        self._tasks = TaskRepository(session)
        self._topics = TopicRepository(session)

    async def resolve(self, place_id: uuid.UUID) -> Place | None:
        """The place *place_id* names, or None if it names nothing.

        Tasks are asked first. The two id spaces do not overlap — every id is a
        uuid4, and the ids tasks inherited belong to `topics` rows that the same
        migration deleted — so the order is a cost decision, not a correctness
        one: work is what gets addressed, rooms are what get opened.
        """
        task = await self._tasks.get(place_id)
        if task is not None:
            room = await self._topics.get(task.room_id)
            # A task whose room is gone cannot be rendered anywhere. Returning
            # None says that plainly instead of handing back half a place that
            # every caller would then have to check.
            return Place(room=room, task=task) if room is not None else None
        topic = await self._topics.get(place_id)
        return Place(room=topic) if topic is not None else None
