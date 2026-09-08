"""Read complete conversation records without triggering agent work."""

import asyncio
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.integration.conftest import session_auth_headers
from tests.unit.test_cheese_cli import _load


def _room(client):
    response = client.post(
        "/projects", json={"name": "History", "owner_handle": "alice"}
    )
    assert response.status_code == 200, response.text
    project = response.json()["data"]
    return project["id"], project["root_topic_id"]


def _seed(client, project, room, entries):
    ids = []

    async def write():
        async with client.test_factory() as session:
            start = datetime(2026, 9, 8, tzinfo=UTC)
            for i, entry in enumerate(entries):
                values = {
                    "id": uuid.uuid4(),
                    "project_id": uuid.UUID(project),
                    "topic_id": uuid.UUID(room),
                    "author": "alice",
                    "author_type": AuthorType.human,
                    "kind": BlockKind.message,
                    "content": f"message {i}",
                    "created_at": start + timedelta(seconds=i),
                    "updated_at": start,
                    **entry,
                }
                session.add(Block(**values))
                ids.append(str(values["id"]))
            await session.commit()

    asyncio.run(write())
    return ids


def _history(client, room, **params):
    response = client.get(f"/topics/{room}/history", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_history_walks_both_directions_with_no_gaps(client):
    project, room = _room(client)
    ids = _seed(client, project, room, [{} for _ in range(57)])
    latest = _history(client, room)
    assert [b["id"] for b in latest["data"]] == ids[-50:]
    assert latest["has_more"] is True
    older = _history(client, room, before=latest["oldest_id"])
    assert [b["id"] for b in older["data"]] == ids[:7]
    assert older["has_more"] is False
    forward = _history(client, room, after=ids[0], limit=3)
    assert [b["id"] for b in forward["data"]] == ids[1:4]
    assert forward["has_more"] is True
    next_page = _history(client, room, after=forward["newest_id"], limit=3)
    assert [b["id"] for b in next_page["data"]] == ids[4:7]


def test_search_covers_old_messages_and_treats_sql_wildcards_literally(client):
    project, room = _room(client)
    ids = _seed(
        client,
        project,
        room,
        [
            {"content": "Old ERROR 50%_\\path"},
            {"content": "ERROR 50xxZpath"},
            *[{} for _ in range(55)],
        ],
    )
    data = _history(client, room, q="error 50%_\\path", limit=1)
    assert [b["id"] for b in data["data"]] == ids[:1]
    assert data["has_more"] is False


def test_search_finds_special_payloads_and_quoted_comments(client):
    project, room = _room(client)
    ids = _seed(
        client,
        project,
        room,
        [
            {
                "kind": BlockKind.event,
                "content": "Build failed",
                "meta": {"detail": "配置文件缺失", "event_type": "ci_failed"},
            },
            {
                "kind": BlockKind.comment,
                "anchor_quote": "quoted contract",
                "content": "Revise this",
            },
        ],
    )
    assert _history(client, room, q="配置文件缺失")["data"][0]["id"] == ids[0]
    assert _history(client, room, q="quoted contract")["data"][0]["id"] == ids[1]


def test_replies_include_parent_reactions_and_nested_reply_links(client):
    project, room = _room(client)
    parent, child, grandchild = [uuid.uuid4() for _ in range(3)]
    _seed(
        client,
        project,
        room,
        [
            {
                "id": parent,
                "content": "Which dataset?",
                "meta": {
                    "options": ["A", "B"],
                    "answered": "B",
                    "answered_by": "alice",
                },
            },
            {"id": child, "content": "Use B", "reply_to": parent},
            {"id": grandchild, "content": "Confirmed", "reply_to": child},
            {"content": "Unrelated"},
        ],
    )
    for block_id in (parent, child):
        response = client.post(
            f"/blocks/{block_id}/reactions", json={"emoji": "👍", "author": "alice"}
        )
        assert response.status_code == 200, response.text
    data = _history(client, room, reply_to=str(parent))
    assert [b["id"] for b in data["data"]] == [str(child)]
    assert data["reply_to"]["content"] == "Which dataset?"
    assert data["reply_to"]["meta"]["answered"] == "B"
    for item in (data["reply_to"], data["data"][0]):
        assert item["reactions"] == [{"emoji": "👍", "count": 1, "authors": ["alice"]}]
    nested = _history(client, room, reply_to=str(child))
    assert nested["data"][0]["id"] == str(grandchild)
    assert nested["data"][0]["reply_to"] == str(child)


@pytest.mark.parametrize("kind", list(BlockKind))
def test_every_block_kind_can_be_read_with_its_complete_fields(client, kind):
    project, room = _room(client)
    block_id = _seed(
        client,
        project,
        room,
        [
            {
                "kind": kind,
                "content": "uploads/report.pdf",
                "mime_type": "application/pdf",
                "anchor_quote": "paragraph",
                "meta": {
                    "filename": "report.pdf",
                    "size": 12345,
                    "future": {"payload": ["x"]},
                },
                "refs": ["reference"],
            }
        ],
    )[0]
    response = client.get(f"/topics/{room}/history/{block_id}")
    assert response.status_code == 200, response.text
    block = response.json()["data"]
    assert block["kind"] == kind.value
    assert block["content"] == "uploads/report.pdf"
    assert block["mime_type"] == "application/pdf"
    assert block["anchor_quote"] == "paragraph"
    assert block["meta"]["future"] == {"payload": ["x"]}
    assert block["refs"] == ["reference"]
    assert _history(client, room, kind=kind.value)["data"][0] == block


def test_room_history_and_task_card_history_have_separate_scopes(client):
    project, room = _room(client)
    result = client.post(
        f"/topics/{room}/split", json={"title": "Investigate", "brief": "Read the logs"}
    )
    assert result.status_code == 200, result.text
    task = result.json()["data"]["id"]
    parent, child = uuid.uuid4(), uuid.uuid4()
    ids = _seed(
        client,
        project,
        room,
        [
            {"content": "Room message"},
            {"id": parent, "task_id": uuid.UUID(task), "content": "Task question"},
            {
                "id": child,
                "task_id": uuid.UUID(task),
                "content": "Task reply",
                "reply_to": parent,
            },
        ],
    )
    assert ids[1] not in [b["id"] for b in _history(client, room)["data"]]
    assert [
        b["id"] for b in _history(client, room, task_id=task, q="Task")["data"]
    ] == ids[1:]
    replies = _history(client, room, reply_to=str(parent))
    assert replies["data"][0]["id"] == str(child)
    assert replies["data"][0]["task_id"] == task
    assert (
        client.get(f"/topics/{room}/history", params={"before": str(child)}).status_code
        == 404
    )


def test_reads_do_not_mark_input_consumed_or_wake_agent(client, stub_hooks):
    project, room = _room(client)
    block_id = _seed(client, project, room, [{"meta": {"consumed_turn": None}}])[0]
    _history(client, room)
    response = client.get(f"/topics/{room}/history/{block_id}")
    assert response.json()["data"]["meta"]["consumed_turn"] is None
    assert stub_hooks.last_prompt is None


def test_room_authorization_applies_to_all_history_reads(client):
    project, room = _room(client)
    block = _seed(client, project, room, [{}])[0]
    headers = session_auth_headers("outsider")
    for suffix in ("", f"/{block}", f"?reply_to={block}", "?q=message"):
        response = client.get(f"/topics/{room}/history{suffix}", headers=headers)
        assert response.status_code == 403, response.text


def test_foreign_message_ids_cannot_be_used_as_reads_cursors_or_reply_targets(client):
    project, room = _room(client)
    _, other = _room(client)
    block = _seed(client, project, room, [{}])[0]
    for suffix in (
        f"/{block}",
        f"?before={block}",
        f"?after={block}",
        f"?reply_to={block}",
    ):
        assert client.get(f"/topics/{other}/history{suffix}").status_code == 404


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 501}, {"q": ""}, {"kind": "unknown"}]
)
def test_invalid_query_is_rejected(client, params):
    _, room = _room(client)
    assert client.get(f"/topics/{room}/history", params=params).status_code == 400


def test_cli_reads_replies_through_the_real_route(client, monkeypatch, capsys):
    project, room = _room(client)
    parent, child = uuid.uuid4(), uuid.uuid4()
    _seed(
        client,
        project,
        room,
        [
            {"id": parent, "content": "Review the attached document"},
            {
                "id": child,
                "kind": BlockKind.attachment,
                "reply_to": parent,
                "content": "uploads/spec.pdf",
                "mime_type": "application/pdf",
                "meta": {"filename": "spec.pdf", "size": 500},
            },
        ],
    )
    client.post(f"/blocks/{child}/reactions", json={"emoji": "👍", "author": "alice"})
    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", room)
    monkeypatch.setattr(cli, "API", "http://testserver")

    def local_http(request, timeout):
        response = client.get(request.full_url, headers=dict(request.header_items()))
        assert response.status_code == 200, response.text
        return io.BytesIO(response.content)

    monkeypatch.setattr(cli.urllib.request, "urlopen", local_http)
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "chat", "replies", str(parent), "--json"]
    )
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["reply_to"]["id"] == str(parent)
    reply = result["data"][0]
    assert reply["content"] == "uploads/spec.pdf"
    assert reply["mime_type"] == "application/pdf"
    assert reply["meta"]["filename"] == "spec.pdf"
    assert reply["reactions"] == [{"emoji": "👍", "count": 1, "authors": ["alice"]}]
