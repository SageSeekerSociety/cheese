"""A channel's conversation is handed a member's `communication` notes in the
turn that first answers that member, and again only once they changed; a turn
answering someone else never carries them.
"""

import uuid

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.memory.files import INDEX_NAME, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.writers import MemoryWriter
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

ALICES = "alice-wants-short-replies-5521"
ALICES_NOW = "alice-now-wants-a-summary-first-5521"
BOBS = "bob-wants-an-at-5521"


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


def _write(client, project: str, owner: str, name: str, body: str) -> None:
    async def write(session):
        store = MemoryFileStore(session)
        for path, content in (
            (f"{name}.md", _note(name, body)),
            (INDEX_NAME, f"- [{name}]({name}.md) — {body}\n"),
        ):
            rows = await store.list(uuid.UUID(project), MemoryFileScope.private, owner)
            held = {row.path: row.version for row in rows}
            await store.write(
                project_id=uuid.UUID(project),
                scope=MemoryFileScope.private,
                owner_handle=owner,
                path=path,
                content=content,
                updated_by=owner,
                expected_version=held.get(path),
                writer=MemoryWriter.panel,
            )

    _db(client, write)


def _channel(client) -> tuple[str, str]:
    project = post_project(client, {"name": "Map"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "失物招领"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    _write(client, project["id"], "alice", "reply-short", ALICES)
    _write(client, project["id"], "bob", "at-him", BOBS)
    return project["id"], room


def _turns(client, tmp_path, room: str):
    screen = StubChannel()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = uuid.UUID(room)

    def turn(author: str, content: str) -> str:
        async def run():
            async for _ in service.converse(
                topic_id=topic_id, author=author, content=content, summon=True
            ):
                pass
            await settle_turn(service, topic_id)

        client.portal.call(run)
        return screen.last_prompt or ""

    return turn


def test_a_member_s_notes_are_given_once_and_again_when_they_change(client, tmp_path):
    project, room = _channel(client)
    turn = _turns(client, tmp_path, room)

    first = turn("alice", "@芝士 报名表做到哪了")
    assert ALICES in first
    assert BOBS not in first
    assert "先用 Read 读" not in first

    again = turn("alice", "@芝士 那就这样")
    assert ALICES not in again

    _write(client, project, "alice", "reply-short", ALICES_NOW)
    changed = turn("alice", "@芝士 还有一件事")
    assert ALICES_NOW in changed
    assert ALICES not in changed

    bobs = turn("bob", "@芝士 我来帮忙")
    assert BOBS in bobs
    assert ALICES_NOW not in bobs and ALICES not in bobs


def test_a_turn_with_no_notes_carries_no_section(client, tmp_path):
    project = post_project(client, {"name": "Map"}, owner="carol").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "失物招领"},
        headers=session_auth_headers("carol"),
    ).json()["data"]["id"]
    turn = _turns(client, tmp_path, room)

    said = turn("carol", "@芝士 报名表做到哪了")

    assert "communication" not in said
