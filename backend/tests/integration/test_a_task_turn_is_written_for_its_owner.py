"""A turn in a task's own conversation reads the task owner's private memory.

The task's document is written for its owner, the way he reads, so his
preferences have to be in reach even on a turn he said nothing in (a platform
instruction started it). Someone else's are not.
"""

import uuid

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.memory.files import INDEX_NAME, MemoryFileScope, prompt_path
from app.domain.memory.files_store import MemoryFileStore
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
            )

    _db(client, lay)
    return task


def test_a_task_turn_reads_its_silent_owners_memory(client, tmp_path):
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

    # The owner's index is given as where it is; the session reads it.
    owners = prompt_path(f"private/{OWNER}/{INDEX_NAME}")
    assert owners in screen.told, "the owner's private memory was not in reach"
    assert OWNER_HOOK not in screen.told
    assert prompt_path(f"private/bob/{INDEX_NAME}") not in screen.told
