"""A turn in a task's own conversation carries nobody's private memory, the
task owner's included.

A task's conversation is the shared room's: its session goes on to work for
whoever speaks there next, and what it read for one person would still be in
reach of the next one (`turn_speakers`). Only a person's private chat carries
their private memory.
"""

import uuid

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.memory.files import INDEX_NAME, MemoryFileScope, prompt_path
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.writers import MemoryWriter
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import open_task, post_project, session_auth_headers

OWNER = "alice"
OWNER_HOOK = "- [他要结论在最前面](conclusion-first.md) — 负责人那一份的钩子"
BOB_HOOK = "- [别人的](elsewhere.md) — 不负责这件事的人那一份的钩子"


class Screen(StubChannel):
    """A harness that answers each turn with one line and keeps what it was handed."""

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="task-owner")
        self.stops(screen, "Done.", session_id="task-owner")
        return True


def _db(client, fn):
    async def go():
        async with client.test_request_factory() as session:
            out = await fn(session)
            await session.commit()
            return out

    return client.portal.call(go)


def _task(client) -> str:
    project = post_project(client, {"name": "Map"}, owner=OWNER).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "失物招领"},
        headers=session_auth_headers(OWNER),
    ).json()["data"]["id"]
    task = open_task(client, room, "认领按钮", owner=OWNER)["id"]

    async def lay(session):
        store = MemoryFileStore(session)
        for owner, hook in ((OWNER, OWNER_HOOK), ("bob", BOB_HOOK)):
            await store.write(
                project_id=uuid.UUID(project["id"]),
                scope=MemoryFileScope.private,
                owner_handle=owner,
                path=INDEX_NAME,
                content=hook,
                updated_by="cheese",
                expected_version=None,
                writer=MemoryWriter.panel,
            )

    _db(client, lay)
    return task


def test_a_task_turn_carries_nobodys_private_memory(client, tmp_path):
    task = _task(client)
    screen = Screen()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = uuid.UUID(task)

    async def run():
        async for _ in service.converse(
            topic_id=topic_id, author="system", content="检查红了，看一下", summon=True
        ):
            pass
        await settle_turn(service, topic_id)

    client.portal.call(run)

    for absent in (
        prompt_path(f"private/{OWNER}/{INDEX_NAME}"),
        OWNER_HOOK,
        prompt_path(f"private/bob/{INDEX_NAME}"),
        BOB_HOOK,
    ):
        assert absent not in screen.told
