"""The 现场 hides the platform's repeated reminders, through the route a
reader opens.

The unit tests pin the rule; this one pins the wiring — the projection route
actually applies it, and the raw record it projects from is left alone.
"""

import uuid
from datetime import UTC, datetime

from app.domain.agent.harness.notices import (
    PLATFORM_NOTICE,
    platform_prompt,
    publication_prompt,
    silence_reminder,
)
from app.domain.session_record.models import SessionRecord
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _room(client) -> str:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    return client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def _input(client, room_id: str, text: str) -> None:
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
                                    "content": [{"type": "text", "text": text}]
                                },
                            },
                        }
                    ],
                )
            )
            await db.commit()

    client.portal.call(add)


def _site(client, room_id: str) -> list[dict]:
    response = client.get(
        f"/topics/{room_id}/transcript", headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["data"]


def test_the_site_keeps_the_words_and_drops_the_repeat(client):
    room = _room(client)
    _input(client, room, publication_prompt("先看分页接口"))
    _input(client, room, platform_prompt(silence_reminder(10)))

    blocks = _site(client, room)
    said = [
        part.get("text", "")
        for block in blocks
        for part in (block.get("meta") or {}).get("parts", [])
    ]
    assert "先看分页接口" in said
    assert not any(PLATFORM_NOTICE in text for text in said), said
    assert not any("分钟了还没回" in text for text in said), said

    # The record is untouched: 现场 is a projection, not an edit.
    records = [str((block.get("meta") or {}).get("record")) for block in blocks]
    assert any(PLATFORM_NOTICE in record for record in records), records
