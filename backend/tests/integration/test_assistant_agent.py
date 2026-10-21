"""What a person's 芝士 reads with (`routes/assistant_agent.py`): things by the
words of their address, as the person, in text."""

import asyncio
import uuid

from sqlalchemy import select

from app.core.sandbox_auth import mint_delegated_credential
from app.domain.project.models import Project
from app.domain.topic.models import Topic
from tests.conftest import seed_user
from tests.integration.test_assistant import _ledger_user
from tests.integration.test_message_edit import _room, _say


def _as(token: str) -> dict:
    return {"X-Cheese-Token": token}


def _personal(client, handle: str, project: str | None = None) -> str:
    seed_user(client, handle)
    return mint_delegated_credential(
        user_id=_ledger_user(client, handle)[0],
        handle=handle,
        work=str(uuid.uuid4()),
        project_id=project,
        ttl_s=300,
    )


def _names(client, room: str) -> tuple[str, int]:
    """The room's project's slug and the room's number."""

    async def read():
        async with client.test_factory() as db:
            topic = await db.get(Topic, uuid.UUID(room))
            slug = await db.scalar(
                select(Project.slug).where(Project.id == topic.project_id)
            )
            return slug, topic.number

    return asyncio.run(read())


def _read(client, token: str, ref: str, page: int = 1):
    return client.post(
        "/assistant/agent/read", json={"ref": ref, "page": page}, headers=_as(token)
    )


def test_a_channel_reads_by_its_address_with_its_latest_messages(client):
    room, _ = _room(client)
    _say(client, room, "alice", "周五的组会改到下午三点")
    slug, number = _names(client, room)
    token = _personal(client, "alice")

    r = _read(client, token, f"{slug}/channels/{number}")

    assert r.status_code == 200, r.text
    text = r.json()["data"]["text"]
    assert f"频道「话题」（{slug}/channels/{number}）" in text
    assert "alice：周五的组会改到下午三点" in text
    # A link copied from the address bar reads the same.
    copied = _read(client, token, f"https://x.test/projects/{slug}/channels/{number}")
    assert copied.json()["data"]["text"] == text


def test_a_project_lists_its_channels_by_the_refs_that_read_them(client):
    room, _ = _room(client)
    slug, number = _names(client, room)

    r = _read(client, _personal(client, "alice"), slug)

    assert r.status_code == 200, r.text
    assert f"「话题」（{slug}/channels/{number}）" in r.json()["data"]["text"]


def test_what_the_person_cannot_open_cannot_be_read(client):
    room, _ = _room(client)
    slug, number = _names(client, room)
    outsider = _personal(client, "carol")

    assert _read(client, outsider, slug).status_code in (403, 404)
    assert _read(client, outsider, f"{slug}/channels/{number}").status_code in (
        403,
        404,
    )


def test_a_ref_it_cannot_read_says_how_refs_are_written(client):
    r = _read(client, _personal(client, "alice"), "频道 3")
    assert r.status_code == 400
    assert "ml-course/channels/3" in r.text


def test_the_inbox_says_when_nothing_waits(client):
    r = client.post(
        "/assistant/agent/inbox", json={}, headers=_as(_personal(client, "dora"))
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == "现在没有等你处理的事。"


def test_a_search_finds_across_the_persons_projects_with_refs(client):
    room, _ = _room(client)
    _say(client, room, "alice", "部署要改成 8443 端口")
    slug, number = _names(client, room)

    r = client.post(
        "/assistant/agent/search",
        json={"query": "8443"},
        headers=_as(_personal(client, "alice")),
    )

    assert r.status_code == 200, r.text
    text = r.json()["data"]["text"]
    assert f"项目「P」（{slug}）" in text
    assert f"在频道「话题」（{slug}/channels/{number}）" in text
    # Someone not in the project finds nothing of it.
    other = client.post(
        "/assistant/agent/search",
        json={"query": "8443"},
        headers=_as(_personal(client, "carol")),
    )
    assert "没有搜到" in other.json()["data"]["text"]
