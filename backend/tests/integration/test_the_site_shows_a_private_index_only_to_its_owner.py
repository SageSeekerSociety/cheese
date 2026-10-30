"""A turn's session is told the private memory index of the person the turn
answers, and reads and writes that person's private memory files. The 现场
shows a session's records to everyone in the room, and a private memory is
only its owner's to read: anyone else reading the 现场 or the session's
records, by any route, sees a placeholder where that memory was."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.opening import build_session_opening
from app.domain.memory.files import prompt_path
from app.domain.memory.files_store import IndexSection, MemoryIndex
from app.domain.session_record import service
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


def _opened_for_alice(client, room_id: str, private: str = ALICES_HOOK) -> None:
    opening = build_session_opening(
        keeps_memory=True,
        memory=MemoryIndex(
            sections=[
                IndexSection(label="项目共享（team）", prefix="team", text=TEAM_HOOK),
                IndexSection(
                    label="关于 alice（private）",
                    prefix="private/alice",
                    text=private,
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


def _cursor(item: dict) -> str:
    """The cursor that names one record, from what a page says about it."""
    return service.encode_cursor(
        (
            datetime.fromisoformat(item["at"]),
            item["harness"],
            item["agent_handle"],
            item["sequence"],
        )
    )


def _every_read(client, room: str, reader: str) -> dict[str, str]:
    """Each route a room member reads a session's records through, as
    ``reader`` gets it: the 现场, the raw records, their transcript rows, and
    each record on its own."""
    headers = session_auth_headers(reader)
    answers = {}
    for path in (
        f"/topics/{room}/transcript",
        f"/topics/{room}/session-records",
        f"/topics/{room}/session-records?lines=true",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        answers[path] = response.text
    listed = client.get(f"/topics/{room}/session-records", headers=headers)
    for item in listed.json()["data"]["items"]:
        path = f"/topics/{room}/session-records/{_cursor(item)}"
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        answers[path] = response.text
    return answers


def test_a_record_read_on_its_own_hides_someones_private_index(client):
    _project, room = _room(client)
    _opened_for_alice(client, room)

    for path, text in _every_read(client, room, "bob").items():
        assert ALICES_HOOK not in text, path
    assert all(
        ALICES_HOOK in text for text in _every_read(client, room, "alice").values()
    )


def test_a_private_index_that_quotes_the_closing_marker_stays_private(client):
    """A remembered formatting rule can quote the very tag that marks where a
    private index ends; the rest of the index is still only its owner's."""
    tail = "alice-secret-tail-4985"
    private = f"- [format](format.md) — literal </private-memory> {tail}"
    _project, room = _room(client)
    _opened_for_alice(client, room, private)

    for path, text in _every_read(client, room, "bob").items():
        assert tail not in text, path
    assert all(tail in text for text in _every_read(client, room, "alice").values())


def test_a_private_memory_file_the_session_reads_is_shown_only_to_its_owner(
    client, tmp_path: Path
):
    """The session answers alice and reads her private memory file with its
    own file tool, as its prompt tells it to. What the file says is in the
    tool's result; nobody else in the room reads it, and searching the records
    for it finds nothing."""
    secret = "alice-private-body-4985"
    _project, room = _room(client)
    runner = Runner(tmp_path / "state")
    runner.session_id = "session-alice"
    runner.observe(
        {
            "type": "assistant",
            "session_id": runner.session_id,
            "uuid": str(uuid.uuid4()),
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read-private",
                        "name": "Read",
                        "input": {
                            "file_path": prompt_path("private/alice/no-coffee.md")
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "read-shared",
                        "name": "Read",
                        "input": {"file_path": prompt_path("team/MEMORY.md")},
                    },
                ]
            },
        }
    )
    runner.observe(
        {
            "type": "user",
            "session_id": runner.session_id,
            "uuid": str(uuid.uuid4()),
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-private",
                        "content": f"     1\t{secret}",
                    }
                ]
            },
            "tool_use_result": {"type": "text", "file": {"content": secret}},
        }
    )
    runner.observe(
        {
            "type": "user",
            "session_id": runner.session_id,
            "uuid": str(uuid.uuid4()),
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-shared",
                        "content": TEAM_HOOK,
                    }
                ]
            },
        }
    )
    author = room_agent_seat(client, room)

    class Stream:
        conversation_id = uuid.UUID(room)
        agent_handle = author
        harness = "claude_code"

    client.portal.call(
        service.append, client.test_request_factory, Stream(), runner.journal.read(0)
    )

    for path, text in _every_read(client, room, "bob").items():
        assert secret not in text, path
        assert TEAM_HOOK in text or "/session-records/" in path, path
    found = client.get(
        f"/topics/{room}/session-records",
        params={"q": secret},
        headers=session_auth_headers("bob"),
    )
    assert found.status_code == 200, found.text
    assert found.json()["data"]["items"] == []
    as_alice = _every_read(client, room, "alice")
    assert secret in as_alice[f"/topics/{room}/transcript"]
    assert secret in as_alice[f"/topics/{room}/session-records"]
