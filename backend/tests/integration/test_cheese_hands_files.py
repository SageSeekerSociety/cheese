"""A person's 芝士 moves files between its computer and the person's own
library: it is given an address for one file at a time, puts what it made
there and hands it over, fetches what the person gave it from there, and
lists and reads the library; nobody else's library is within its reach.

The computer's side (a `curl` to those addresses) is the tools' own test,
`tests/unit/test_cheese_file_tools.py`; here the addresses are used the way
the computer would.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.sandbox_auth import mint_delegated_credential
from app.domain.team.services import team_service
from tests.conftest import seed_user
from tests.integration.test_assistant import _ledger_user


def _acting(client, handle: str) -> dict:
    seed_user(client, handle)
    return {
        "Authorization": f"Bearer {
            mint_delegated_credential(
                user_id=_ledger_user(client, handle)[0],
                handle=handle,
                work=str(uuid.uuid4()),
                ttl_s=300,
                read_only=False,
            )
        }"
    }


def _as_person(client, handle: str) -> dict:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _own_team(client, handle: str) -> int:
    user_id = _ledger_user(client, handle)[0]

    async def ensure() -> int:
        async with client.test_factory() as db:
            team = await team_service(db).ensure_personal_team(user_id)
            await db.commit()
            return team.id

    return asyncio.run(ensure())


def _here(url: str) -> str:
    """The address as the test client takes it: the platform's own, without
    its origin and API prefix."""
    origin = f"{settings.frontend_url.rstrip('/')}/api"
    assert url.startswith(origin), url
    return url[len(origin) :]


def _give(client, cheese: dict, name: str, data: bytes) -> dict:
    slot = client.post(
        "/assistant/agent/files/slot",
        json={"name": name.rsplit("/", 1)[-1], "size": len(data)},
        headers=cheese,
    )
    assert slot.status_code == 200, slot.text
    upload = slot.json()["data"]["upload"]
    digest = hashlib.md5(data).hexdigest()
    address = client.post(
        "/assistant/agent/files/part",
        json={"upload": upload, "part": 1, "md5": digest},
        headers=cheese,
    )
    assert address.status_code == 200, address.text
    put = client.put(_here(address.json()["data"]["url"]), content=data)
    assert put.status_code == 200, put.text
    handed = client.post(
        "/assistant/agent/files/handed",
        json={
            "upload": upload,
            "sha256": hashlib.sha256(data).hexdigest(),
            "parts": [digest],
            "name": name,
        },
        headers=cheese,
    )
    assert handed.status_code == 200, handed.text
    return handed.json()["data"]


def test_a_file_cheese_hands_over_is_in_the_persons_library(client):
    cheese = _acting(client, "alice")
    handed = _give(client, cheese, "数据结构/成绩分布.png", b"PNG...")
    assert handed == {"path": "数据结构/成绩分布.png", "bytes": 6}

    alice = _as_person(client, "alice")
    base = f"/teams/{_own_team(client, 'alice')}/library"
    raw = client.get(f"{base}/raw", params={"path": handed["path"]}, headers=alice)
    assert raw.content == b"PNG..."


def test_cheese_fetches_what_the_person_gave_it_and_reads_the_library(client):
    cheese = _acting(client, "alice")
    _give(client, cheese, "成绩.csv", b"name,score\nan,92\n")

    listed = client.post(
        "/assistant/agent/library", json={"ref": "library"}, headers=cheese
    )
    assert "library/成绩.csv" in listed.json()["data"]["text"]
    read = client.post(
        "/assistant/agent/library", json={"ref": "library/成绩.csv"}, headers=cheese
    )
    assert "an,92" in read.json()["data"]["text"]

    link = client.post(
        "/assistant/agent/files/link", json={"path": "library/成绩.csv"}, headers=cheese
    ).json()["data"]
    assert link["bytes"] == len(b"name,score\nan,92\n")
    fetched = client.get(_here(link["url"]))
    assert fetched.content == b"name,score\nan,92\n"


def test_an_address_is_good_for_its_one_file_only(client):
    cheese = _acting(client, "alice")
    _give(client, cheese, "a.txt", b"aaa")
    link = client.post(
        "/assistant/agent/files/link", json={"path": "a.txt"}, headers=cheese
    ).json()["data"]
    token = urlsplit(link["url"]).path.rsplit("/", 1)[-1]
    assert client.get(f"/files/outgoing/{token[:-2]}xx").status_code == 422


def test_nobody_elses_library_is_within_reach(client):
    _give(client, _acting(client, "alice"), "私人.txt", b"x")
    bob = _acting(client, "bob")
    assert (
        client.post(
            "/assistant/agent/files/link", json={"path": "私人.txt"}, headers=bob
        ).status_code
        == 404
    )
    listed = client.post(
        "/assistant/agent/library", json={"ref": "library"}, headers=bob
    )
    assert "私人.txt" not in listed.json()["data"]["text"]


def test_a_full_library_takes_nothing_more(client, monkeypatch):
    monkeypatch.setattr(settings, "team_library_bytes", 8)
    cheese = _acting(client, "alice")
    _give(client, cheese, "a.txt", b"123456")
    refused = client.post(
        "/assistant/agent/files/slot", json={"name": "b.txt", "size": 6}, headers=cheese
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["i18n"]["key"] == "libraryFull"
