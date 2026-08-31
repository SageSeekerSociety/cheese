"""`GET /projects/{id}/tasks` says 「运行中」 while the agent is actually working.

The unit tests around the runner pin the row; this pins the thing anyone can
actually see. It matters separately because the row and the screen are two
hops apart — the board reads `presentation`, which reads residency — and the
whole symptom was a project where 243 threads all reported 「空闲」 while
several of them were pushing commits.

Driven through a real turn rather than by writing `running` into the row: what
broke was never the writing, it was WHEN the runner took it back.
"""

import asyncio
import uuid

from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.project.services import ProjectService
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService


class _SessionChat:
    """The interactive harness: `converse` injects the prompt, says the live
    session owns the ending, and returns. The agent's work — and the end of it —
    happens afterwards, in the session."""

    def __init__(self, session_factory, broker):
        self.session_factory = session_factory
        self._broker = broker
        self._live: dict[uuid.UUID, uuid.UUID] = {}
        self.last_turn_id: uuid.UUID | None = None

    async def work_policy(self, topic_id):
        return {
            "project_id": "p",
            "max_concurrent_turns": 8,
            "credits_exhausted": False,
        }

    async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
        return {"content": content, "meta": meta}

    def has_running_turn(self, topic_id) -> bool:
        return topic_id in self._live

    def session_took_over(self, topic_id, turn_id) -> bool:
        return self._live.get(topic_id) == turn_id

    async def merge_into_running_turn(self, *args):
        return None

    async def converse(self, **kwargs):
        self.last_turn_id = kwargs["turn_id"]
        yield {"type": "session_lifecycle"}

    async def opens_its_activity(self, topic_id):
        self._live[topic_id] = self.last_turn_id
        await self._broker.publish(
            str(topic_id),
            {"type": "turn_started", "turn_id": str(self.last_turn_id)},
        )

    async def stops(self, topic_id):
        turn_id = self._live.pop(topic_id, None)
        await self._broker.publish(
            str(topic_id), {"type": "turn_finished", "turn_id": str(turn_id)}
        )


def test_a_working_thread_reads_as_running_over_the_api(client):
    ids: dict[str, str] = {}
    live: dict = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = await ProjectService(s).create(name="P", owner_handle="alice")
            room = await TopicService(s).create(
                project_id=project.id, title="房间", created_by="alice"
            )
            task = await TaskService(s).open_thread(
                project_id=project.id,
                room_id=room.id,
                title="在跑的活",
                owner_handle="alice",
                created_by="alice",
                agent_instance_id=None,
            )
            ids.update(project=str(project.id), task=str(task.id))
            live["task_id"] = task.id
            await s.commit()

    async def _start_the_turn() -> None:
        broker = InProcessBroker()
        runner = AgentWorkRunner(broker, turn_timeout_s=20.0)
        chat = _SessionChat(client.test_factory, broker)
        live.update(broker=broker, runner=runner, chat=chat)
        done = asyncio.Event()
        runner.submit(
            chat,
            live["task_id"],
            author="alice",
            content="干活",
            summon=True,
            on_done=done.set,
        )
        async with asyncio.timeout(10):
            await done.wait()
        # The session opens its activity after the injecting request returned —
        # the order the real one uses, and the order that used to lose the slot.
        await live["chat"].opens_its_activity(live["task_id"])
        await asyncio.sleep(0.3)

    async def _stop_the_agent() -> None:
        await live["chat"].stops(live["task_id"])
        await asyncio.sleep(0.3)

    client.portal.call(_seed)
    client.portal.call(_start_the_turn)

    working = client.get(f"/projects/{ids['project']}/tasks").json()["data"]["data"]
    row = next(r for r in working if r["id"] == ids["task"])
    assert row["residency"] == "running"
    assert row["presentation"]["display_status"] == "运行中"

    client.portal.call(_stop_the_agent)

    quiet = client.get(f"/projects/{ids['project']}/tasks").json()["data"]["data"]
    row = next(r for r in quiet if r["id"] == ids["task"])
    assert row["residency"] == "idle"
    assert row["presentation"]["display_status"] != "运行中"
