"""A turn's session is told the private memory index of the person the turn
answers. The 现场 shows a session's inputs to everyone in the room, and a
private memory is only its owner's to read: anyone else reading the 现场 or
the session's records sees a placeholder where that index was."""

import uuid
from datetime import UTC, datetime

from app.domain.agent.harness.opening import build_session_opening
from app.domain.memory.files_store import IndexSection, MemoryIndex
from app.domain.session_record.models import SessionRecord
from tests.integration.conftest import (
    join_project_team,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

TEAM_HOOK = "- [回答先给结论](answer-first.md) — 项目那一份"
ALICES_HOOK = "- [alice 不喝咖啡](no-coffee.md) — 只是 alice 的"


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return project["id"], room


def _opened_for_alice(client, room_id: str) -> None:
    opening = build_session_opening(
        keeps_memory=True,
        memory=MemoryIndex(
            sections=[
                IndexSection(label="项目共享（team）", prefix="team", text=TEAM_HOOK),
                IndexSection(
                    label="关于 alice（private）",
                    prefix="private/alice",
                    text=ALICES_HOOK,
                    owner="alice",
                ),
            ],
            warnings=[],
        ),
    )
    author = room_agent_seat(client, room_id)

    async def add() -> None:
        async with client.test_request_factory() as db:
            at = datetime.now(UTC)
            db.add(
                SessionRecord(
                    conversation_id=uuid.UUID(room_id),
                    agent_handle=author,
                    harness="claude_code",
                    first_sequence=1,
                    last_sequence=1,
                    first_at=at,
                    last_at=at,
                    count=1,
                    bytes=1,
                    records=[
                        {
                            "sequence": 1,
                            "at": at.isoformat(),
                            "record": {
                                "type": "user",
                                "message": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": f"{opening.text}\n\n说说进展",
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                )
            )
            await db.commit()

    client.portal.call(add)


def test_another_member_reads_the_site_without_someones_private_index(client):
    _project, room = _room(client)
    _opened_for_alice(client, room)

    for path in (
        f"/topics/{room}/transcript",
        f"/topics/{room}/session-records",
        f"/topics/{room}/session-records?lines=true",
    ):
        as_bob = client.get(path, headers=session_auth_headers("bob"))
        assert as_bob.status_code == 200, (path, as_bob.text)
        assert TEAM_HOOK in as_bob.text, path
        assert ALICES_HOOK not in as_bob.text, path

    as_alice = client.get(
        f"/topics/{room}/transcript", headers=session_auth_headers("alice")
    )
    assert as_alice.status_code == 200, as_alice.text
    assert ALICES_HOOK in as_alice.text
