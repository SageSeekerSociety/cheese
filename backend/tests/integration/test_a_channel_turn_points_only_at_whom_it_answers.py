"""A channel turn is told whose `communication` notes to read: the person
whose message it answers, and nobody else. The notes themselves are not in
what it is handed — that is read by the whole channel; only where they are.
"""

import uuid

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.memory.files import INDEX_NAME, MemoryFileScope, prompt_path
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.writers import MemoryWriter
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import post_project, session_auth_headers

OWNER = "alice"
SHORT_BODY = "alice-wants-short-replies-5521"
BOBS_BODY = "bob-wants-an-at-5521"


class Screen(StubChannel):
    """A harness that answers each turn with one line and keeps what it was handed."""

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="channel")
        self.stops(screen, "Done.", session_id="channel")
        return True


def _db(client, fn):
    async def go():
        async with client.test_request_factory() as session:
            out = await fn(session)
            await session.commit()
            return out

    return client.portal.call(go)


def _note(name: str, body: str) -> str:
    return (
        f"---\nname: {name}\ndescription: {body}\ntype: communication\n---\n\n{body}\n"
    )


def _channel(client) -> str:
    project = post_project(client, {"name": "Map"}, owner=OWNER).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "失物招领"},
        headers=session_auth_headers(OWNER),
    ).json()["data"]["id"]

    async def lay(session):
        store = MemoryFileStore(session)
        for owner, name, body in (
            (OWNER, "reply-short", SHORT_BODY),
            ("bob", "at-him", BOBS_BODY),
        ):
            for path, content in (
                (f"{name}.md", _note(name, body)),
                (INDEX_NAME, f"- [{name}]({name}.md) — {body}\n"),
            ):
                await store.write(
                    project_id=uuid.UUID(project["id"]),
                    scope=MemoryFileScope.private,
                    owner_handle=owner,
                    path=path,
                    content=content,
                    updated_by=owner,
                    expected_version=None,
                    writer=MemoryWriter.panel,
                )

    _db(client, lay)
    return room


def test_a_channel_turn_is_pointed_at_the_notes_of_the_person_it_answers(
    client, tmp_path
):
    room = _channel(client)
    screen = Screen()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = uuid.UUID(room)

    async def run():
        async for _ in service.converse(
            topic_id=topic_id, author=OWNER, content="@芝士 报名表做到哪了", summon=True
        ):
            pass
        await settle_turn(service, topic_id)

    client.portal.call(run)

    assert prompt_path(f"private/{OWNER}/{INDEX_NAME}") in screen.told
    for absent in (prompt_path(f"private/bob/{INDEX_NAME}"), SHORT_BODY, BOBS_BODY):
        assert absent not in screen.told
