"""一条活正在跑的时候，它就得占着房间的一个额度。

`test_task_residency.py` covers `ResidencyService` on its own, and it is right:
admit / touch / release / sweep all do exactly what they say. What nothing
covered is the CALLER — the runner, which is what actually takes and gives back
a slot around a real turn. That gap is why a thread could be visibly working
while its room said it was holding nothing.

The distinction that makes this hard, and that every test here is built around:
**a turn does not end when the request that started it returns.** The interactive
harness hands the turn to a live session the moment the prompt is injected, and
the request comes back right then — with the agent about to work for minutes.
Anything that gives the slot back at that point gives it back at the START of
the work.

Residency is not internal bookkeeping: `GET /projects/{id}/tasks` reports it for
every thread, and `admit` counts it. So the assertions here are on those two —
what the row says a caller would be told, and what the room then does with the
next piece of work.
"""

import asyncio
import uuid

import pytest

from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.project.services import ProjectService
from app.domain.room_task.models import MAX_RESIDENT_TASKS_PER_ROOM, Residency
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.services import ResidencyService, TaskService
from app.domain.topic.services import TopicService


class _Chat:
    """Stand-in for ChatService, faithful about WHEN a turn ends.

    Two shapes, because the platform has two:

    * `hands_to_session=True` — the interactive harness. `converse` yields
      `session_lifecycle` ("the live subscription owns the ending from here"),
      injects, opens the session's activity, and returns — in that order,
      because the real one opens the activity inside the send it awaits. The
      agent then works, and `session_finishes` is the actual end of the turn,
      minutes later.
    * `hands_to_session=False` — a plain request-scoped turn: frames stream
      until the turn is actually over, and then it is over.
    """

    def __init__(self, session_factory, broker, *, hands_to_session: bool):
        self.session_factory = session_factory
        self._broker = broker
        self._hands_to_session = hands_to_session
        self.injecting = asyncio.Event()
        self.may_finish_injecting = asyncio.Event()
        self.system_events: list[str] = []
        self._live: dict[uuid.UUID, uuid.UUID] = {}
        self.last_turn_id: uuid.UUID | None = None

    async def work_policy(self, topic_id):
        return {
            "project_id": "p",
            "max_concurrent_turns": 16,
            "credits_exhausted": False,
        }

    async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
        self.system_events.append(content)
        return {"content": content, "meta": meta}

    def has_running_turn(self, topic_id) -> bool:
        return topic_id in self._live

    def session_took_over(self, topic_id, turn_id) -> bool:
        return self._live.get(topic_id) == turn_id

    async def merge_into_running_turn(self, *args):
        return None

    async def converse(self, **kwargs):
        topic_id = kwargs["topic_id"]
        self.last_turn_id = kwargs["turn_id"]
        self.injecting.set()
        if self._hands_to_session:
            yield {"type": "session_lifecycle"}
        await self.may_finish_injecting.wait()
        if self._hands_to_session:
            await self._session_starts_working(topic_id)
        else:
            yield {"type": "done"}

    async def _session_starts_working(self, topic_id):
        """The session opens its activity, still inside the injecting send."""
        self._live[topic_id] = self.last_turn_id
        await self._broker.publish(
            str(topic_id),
            {"type": "turn_started", "turn_id": str(self.last_turn_id)},
        )

    async def session_finishes(self, topic_id):
        """The agent stopped: the session retires its activity. THIS is the end
        of the turn — minutes after the request that started it returned."""
        turn_id = self._live.pop(topic_id, None)
        await self._broker.publish(
            str(topic_id), {"type": "turn_finished", "turn_id": str(turn_id)}
        )


async def _a_room(factory, *, threads: int) -> tuple[uuid.UUID, uuid.UUID, list]:
    """A project, a room in it, and `threads` pieces of work in the room."""
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        room = await TopicService(session).create(
            project_id=project.id, title="房间", created_by="u"
        )
        made = [
            await TaskService(session).open_thread(
                project_id=project.id,
                room_id=room.id,
                title=f"活 {i}",
                owner_handle="u",
                created_by="u",
                agent_instance_id=None,
            )
            for i in range(threads)
        ]
        ids = [t.id for t in made]
        await session.commit()
    return project.id, room.id, ids


async def _residency(factory, task_id) -> Residency:
    """What `GET /projects/{id}/tasks` would say about this thread."""
    async with factory() as session:
        return (await TaskRepository(session).get(task_id)).residency


async def _admits_another(factory, project_id, room_id) -> bool:
    """Would the room take one more piece of work right now?"""
    async with factory() as session:
        task = await TaskService(session).open_thread(
            project_id=project_id,
            room_id=room_id,
            title="再来一条",
            owner_handle="u",
            created_by="u",
            agent_instance_id=None,
        )
        admitted = await ResidencyService(session).admit(task)
        await session.commit()
    return admitted


async def _until(cond, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


async def _until_residency(factory, task_id, want: Residency, timeout=5.0) -> None:
    async with asyncio.timeout(timeout):
        while await _residency(factory, task_id) != want:
            await asyncio.sleep(0.01)


# One runner per broker, kept alive: the process has exactly one of each, and a
# runner also holds the strong references to its own in-flight turns — one that
# goes out of scope can take a running turn down with it.
_RUNNERS: dict[int, AgentWorkRunner] = {}


def _runner(broker) -> AgentWorkRunner:
    return _RUNNERS.setdefault(id(broker), AgentWorkRunner(broker, turn_timeout_s=20.0))


async def _run_one_session_turn(factory, broker, task_id) -> _Chat:
    """Start a turn that hands off to a live session, and return once the
    injecting request has come back — i.e. at the moment the agent is only just
    starting to work."""
    chat = _Chat(factory, broker, hands_to_session=True)
    runner = _runner(broker)
    done = asyncio.Event()
    runner.submit(
        chat,
        task_id,
        author="u",
        content="干活",
        summon=True,
        on_done=done.set,
    )
    await _until(lambda: chat.injecting.is_set())
    # The slot is taken while the turn is being set up — that part has always
    # worked, and waiting for it here is what makes the rest of the test about
    # the ONLY question that matters: does it stay taken.
    await _until_residency(factory, task_id, Residency.running)
    chat.may_finish_injecting.set()
    await _until(lambda: done.is_set())
    return chat


@pytest.mark.anyio
async def test_a_thread_still_holds_its_slot_once_the_session_takes_over(db_factory):
    """The injecting request has returned; the agent has not even begun. This is
    the moment residency was getting thrown away."""
    _, _, (task_id,) = await _a_room(db_factory, threads=1)
    broker = InProcessBroker()

    await _run_one_session_turn(db_factory, broker, task_id)
    await asyncio.sleep(0.1)  # let anything racing to free it get there

    assert await _residency(db_factory, task_id) == Residency.running


@pytest.mark.anyio
async def test_the_slot_comes_back_when_the_agent_actually_stops(db_factory):
    _, _, (task_id,) = await _a_room(db_factory, threads=1)
    broker = InProcessBroker()

    chat = await _run_one_session_turn(db_factory, broker, task_id)
    assert await _residency(db_factory, task_id) == Residency.running

    await chat.session_finishes(task_id)

    await _until_residency(db_factory, task_id, Residency.idle)


@pytest.mark.anyio
async def test_four_working_threads_fill_the_room(db_factory):
    """一个房间最多同时开 4 条: the cap counts running rows, so a cap that never
    sees one is a cap that never fires."""
    project_id, room_id, tasks = await _a_room(
        db_factory, threads=MAX_RESIDENT_TASKS_PER_ROOM
    )
    broker = InProcessBroker()

    for task_id in tasks:
        await _run_one_session_turn(db_factory, broker, task_id)

    assert await _admits_another(db_factory, project_id, room_id) is False


@pytest.mark.anyio
async def test_the_room_takes_new_work_again_once_a_thread_stops(db_factory):
    """The other half: holding a slot for work that ended would wedge the room
    just as badly as never holding one."""
    project_id, room_id, tasks = await _a_room(
        db_factory, threads=MAX_RESIDENT_TASKS_PER_ROOM
    )
    broker = InProcessBroker()

    chats = [await _run_one_session_turn(db_factory, broker, t) for t in tasks]
    await chats[0].session_finishes(tasks[0])
    await _until_residency(db_factory, tasks[0], Residency.idle)

    assert await _admits_another(db_factory, project_id, room_id) is True


@pytest.mark.anyio
async def test_a_request_scoped_turn_holds_its_slot_and_gives_it_back(db_factory):
    """No session takes over: the turn really is over when the request is, and
    the slot must come back then."""
    _, _, (task_id,) = await _a_room(db_factory, threads=1)
    broker = InProcessBroker()
    chat = _Chat(db_factory, broker, hands_to_session=False)
    done = asyncio.Event()

    _runner(broker).submit(
        chat, task_id, author="u", content="干活", summon=True, on_done=done.set
    )
    await _until(lambda: chat.injecting.is_set())
    await _until_residency(db_factory, task_id, Residency.running)

    chat.may_finish_injecting.set()
    await _until(lambda: done.is_set())

    await _until_residency(db_factory, task_id, Residency.idle)


@pytest.mark.anyio
async def test_a_turn_shorter_than_its_own_bookkeeping_still_frees_the_slot(
    db_factory, monkeypatch
):
    """Taking the slot is deliberately not awaited in front of the turn's first
    frame. A turn that ends before that write lands must still not leave the row
    claiming a slot nobody is using — otherwise the room loses one for good, and
    the only thing that ever takes it back is a sweep hours later."""
    from app.domain.room_task import services as svc

    original = svc.ResidencyService.touch

    async def _slow_touch(self, task):
        await asyncio.sleep(0.3)
        await original(self, task)

    monkeypatch.setattr(svc.ResidencyService, "touch", _slow_touch)

    _, _, (task_id,) = await _a_room(db_factory, threads=1)
    broker = InProcessBroker()
    chat = _Chat(db_factory, broker, hands_to_session=False)
    chat.may_finish_injecting.set()  # nothing to wait for: the turn is instant
    done = asyncio.Event()

    _runner(broker).submit(
        chat, task_id, author="u", content="干活", summon=True, on_done=done.set
    )
    await _until(lambda: done.is_set())
    await asyncio.sleep(0.5)  # well past the slow write

    assert await _residency(db_factory, task_id) == Residency.idle
