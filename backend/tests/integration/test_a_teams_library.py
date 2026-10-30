"""A team has a library of its own, and a person's own library is their
personal team's: the team's members put files in, read them, replace, move and
delete them, as in a project's library; nobody else can open it; and it holds
so much and no more.
"""

import asyncio

from app.core.config import settings
from app.domain.team.services import team_service
from app.domain.user.services import user_by_handle
from tests.conftest import seed_user
from tests.support import library_uploads


def _as(client, handle: str) -> dict:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _shared_team(client, owner: dict, handle: str) -> int:
    r = client.post("/teams", json={"name": handle, "handle": handle}, headers=owner)
    assert r.status_code == 201, r.text
    return r.json()["data"]["team"]["id"]


def _personal_team(client, handle: str) -> int:
    async def ensure() -> int:
        async with client.test_factory() as db:
            user = await user_by_handle(db, handle)
            assert user is not None
            team = await team_service(db).ensure_personal_team(user.id)
            await db.commit()
            return team.id

    return asyncio.run(ensure())


def _names(client, base: str, headers: dict) -> list[str]:
    r = client.get(base, params={"flat": True}, headers=headers)
    assert r.status_code == 200, r.text
    return sorted(row["path"] for row in r.json()["data"]["data"])


def test_a_persons_own_library_is_theirs_to_fill_and_empty(client):
    alice = _as(client, "alice")
    base = f"/teams/{_personal_team(client, 'alice')}/library"

    put = library_uploads.put_into(
        client, base, "成绩.xlsx", b"v1", folder="数据结构", headers=alice
    )
    assert put.status_code == 200, put.text
    assert _names(client, base, alice) == ["数据结构/成绩.xlsx"]

    raw = client.get(
        f"{base}/raw", params={"path": "数据结构/成绩.xlsx"}, headers=alice
    )
    assert raw.content == b"v1"

    replaced = library_uploads.replace_in(
        client, base, "数据结构/成绩.xlsx", b"v2", headers=alice
    )
    assert replaced.status_code == 200, replaced.text
    versions = client.get(
        f"{base}/versions", params={"path": "数据结构/成绩.xlsx"}, headers=alice
    ).json()["data"]["versions"]
    assert [v["current"] for v in versions] == [True, False]

    moved = client.post(
        f"{base}/move",
        json={"path": "数据结构/成绩.xlsx", "to": "成绩（期末）.xlsx"},
        headers=alice,
    )
    assert moved.status_code == 200, moved.text
    assert _names(client, base, alice) == ["成绩（期末）.xlsx"]
    raw = client.get(f"{base}/raw", params={"path": "成绩（期末）.xlsx"}, headers=alice)
    assert raw.content == b"v2"

    gone = client.delete(base, params={"path": "成绩（期末）.xlsx"}, headers=alice)
    assert gone.status_code == 200, gone.text
    assert _names(client, base, alice) == []


def test_two_libraries_keep_the_same_name_apart(client):
    """The same name in two libraries is two files."""
    alice = _as(client, "alice")
    team = f"/teams/{_shared_team(client, alice, 'lab-one')}/library"
    mine = f"/teams/{_personal_team(client, 'alice')}/library"
    for base, data in ((team, b"team"), (mine, b"mine")):
        put = library_uploads.put_into(client, base, "说明.md", data, headers=alice)
        assert put.status_code == 200, put.text
    for base, data in ((team, b"team"), (mine, b"mine")):
        raw = client.get(f"{base}/raw", params={"path": "说明.md"}, headers=alice)
        assert raw.content == data


def test_only_the_teams_members_open_it(client):
    alice = _as(client, "alice")
    bob = _as(client, "bob")
    base = f"/teams/{_personal_team(client, 'alice')}/library"
    library_uploads.put_into(client, base, "私人.txt", b"x", headers=alice)

    assert client.get(base, headers=bob).status_code == 403
    assert (
        client.get(f"{base}/raw", params={"path": "私人.txt"}, headers=bob).status_code
        == 403
    )
    put = library_uploads.put_into(client, base, "塞进来.txt", b"x", headers=bob)
    assert put.status_code == 403
    assert client.delete(
        base, params={"path": "私人.txt"}, headers=bob
    ).status_code == (403)
    assert _names(client, base, alice) == ["私人.txt"]


def test_it_holds_so_much_and_says_so(client, monkeypatch):
    monkeypatch.setattr(settings, "team_library_bytes", 10)
    alice = _as(client, "alice")
    base = f"/teams/{_personal_team(client, 'alice')}/library"

    assert (
        library_uploads.put_into(
            client, base, "a.txt", b"123456", headers=alice
        ).status_code
        == 200
    )
    room = client.get(f"{base}/room", headers=alice).json()["data"]
    assert room == {"used": 6, "total": 10}

    full = library_uploads.put_into(client, base, "b.txt", b"123456", headers=alice)
    assert full.status_code == 422
    assert full.json()["error"]["i18n"]["key"] == "libraryFull"
    # A version it was replaced from still takes room.
    refused = library_uploads.replace_in(client, base, "a.txt", b"12345", headers=alice)
    assert refused.status_code == 422
    assert _names(client, base, alice) == ["a.txt"]


def test_a_full_library_says_how_large_the_file_is_and_how_much_room_is_left(
    client, monkeypatch
):
    """The refusal names the file's size, what is left and the whole: a
    reader can tell whether deleting one file would be enough."""
    monkeypatch.setattr(settings, "team_library_bytes", 10)
    alice = _as(client, "alice")
    base = f"/teams/{_personal_team(client, 'alice')}/library"
    library_uploads.put_into(client, base, "a.txt", b"123456", headers=alice)

    full = library_uploads.put_into(client, base, "b.txt", b"1234567", headers=alice)

    assert full.status_code == 422
    said = full.json()["error"]
    assert said["i18n"]["params"] == {"size": "7 B", "free": "4 B", "total": "10 B"}
    assert said["message"] == (
        "无法放进资料库：这份文件 7 B，资料库还剩 4 B（共 10 B）。删除部分文件后重试"
    )


def test_a_persons_own_library_takes_a_file_a_project_would_not(client):
    """A person's library takes what they can attach to 芝士, past a project
    library's ceiling."""
    alice = _as(client, "alice")
    data = b"x" * (20 * 1024 * 1024)
    mine = f"/teams/{_personal_team(client, 'alice')}/library"
    put = library_uploads.put_into(client, mine, "素材.bin", data, headers=alice)
    assert put.status_code == 200, put.text
