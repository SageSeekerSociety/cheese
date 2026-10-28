"""Edits belong to one project agent and survive moves between rooms."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_type.library import preset_types
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from tests.integration.conftest import (
    add_external_member,
    approve_overseas,
    post_project,
    project_service,
    put_on_plan,
    registered,
    session_auth_headers,
)


def test_agents_own_independent_configuration(db_session, _portal, monkeypatch):
    async def run():
        await registered(db_session, "owner")
        project = await project_service(db_session).create(
            owner_handle="owner", name="Agents", forge_kind="github_app"
        )
        service = AgentInstanceService(db_session)
        first = await service.create(
            project_id=project.id,
            handle="first",
            type_name="product-design",
            display_name="First",
        )
        second = await service.create(
            project_id=project.id,
            handle="second",
            type_name="product-design",
            display_name="Second",
        )
        original = dict(second.configuration)
        await service.configure(
            first,
            AgentConfiguration(
                **{**first.configuration, "body": "Only review security"}
            ),
        )
        assert second.configuration == original
        assert (
            await service.system_prompt(service.resolved(first))
        ) == "Only review security"
        # Replacing the creation preset cannot change an existing agent. A
        # preset is where a teammate STARTED, not what it is: editing one is a
        # change to what gets created next, and an agent somebody has been
        # working with must not silently acquire a different role because the
        # catalogue moved under it.
        monkeypatch.setitem(
            preset_types(), "product-design", preset_types()["fullstack-engineer"]
        )
        assert second.configuration == original
        assert await service.system_prompt(service.resolved(second)) == original["body"]

    _portal.call(run)


def test_a_saved_agent_can_override_model_but_not_harness(client):
    """A teammate can select a model while the harness remains project-owned."""
    retired = {"harness"}
    project = post_project(client, json={"name": "Models"}).json()["data"]
    pid = project["id"]

    # Opus is a subscription model, which Free leaves out.
    async def reserve() -> None:
        async with client.test_request_factory() as session:
            await put_on_plan(session, project["team_id"], "reserve")
            await approve_overseas(session, project["team_id"])
            await session.commit()

    client.portal.call(reserve)
    default = client.get(f"/projects/{pid}/agents").json()["data"]["data"][0]
    assert not retired & set(default["configuration"])
    assert default["configuration"]["model"] is None

    client.put(
        f"/projects/{pid}/agents/{default['id']}",
        json={
            "configuration": {
                "body": "Review",
                "model": "opus",
                "harness": "codex",
            }
        },
    )
    saved = client.get(f"/projects/{pid}/agents").json()["data"]["data"][0]
    assert saved["configuration"]["body"] == "Review"
    assert saved["configuration"]["model"] == "opus"
    assert not retired & set(saved["configuration"])


def test_room_switch_preserves_the_selected_agents_role(client):
    pid = post_project(client, json={"name": "Rooms"}, owner="alice").json()["data"][
        "id"
    ]
    agent = client.post(
        f"/projects/{pid}/agents",
        json={"display_name": "Reviewer", "configuration": {"body": "Review"}},
    ).json()["data"]
    for title in ["First room", "Second room"]:
        room = client.post(
            "/topics",
            json={"project_id": pid, "title": title},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        r = client.post(
            f"/topics/{room['id']}/members",
            json={"handle": agent["seat_handle"], "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text
    updated = client.put(
        f"/projects/{pid}/agents/{agent['id']}",
        json={"configuration": {"body": "Review security only"}},
    ).json()["data"]
    assert updated["id"] == agent["id"]
    assert updated["handle"] == agent["handle"]
    assert updated["configuration"]["body"] == "Review security only"


def test_a_teammate_saves_how_hard_it_thinks_and_when_it_compacts(client):
    """Effort and compaction share are a teammate's, saved with its role."""
    project = post_project(client, json={"name": "Effort"}, owner="alice").json()[
        "data"
    ]
    pid = project["id"]
    alice = session_auth_headers("alice")
    default = client.get(f"/projects/{pid}/agents", headers=alice).json()["data"][
        "data"
    ][0]

    saved = client.put(
        f"/projects/{pid}/agents/{default['id']}",
        json={"configuration": {"effort": "high", "compact_percent": 70}},
        headers=alice,
    )
    assert saved.status_code == 200, saved.text
    after = client.get(f"/projects/{pid}/agents", headers=alice).json()["data"]["data"][
        0
    ]
    assert after["configuration"]["effort"] == "high"
    assert after["configuration"]["compact_percent"] == 70

    for refused in ({"effort": "turbo"}, {"compact_percent": 95}):
        answer = client.put(
            f"/projects/{pid}/agents/{default['id']}",
            json={"configuration": refused},
            headers=alice,
        )
        assert answer.status_code == 400, answer.text


def test_a_member_changes_a_teammates_costly_settings_and_the_project_is_told(client):
    """The highest effort, compaction and skills change what every turn of a
    teammate costs or can do. Any member may change them; the project's 综合
    says who changed which, from what to what."""
    project = post_project(client, json={"name": "Advanced"}, owner="alice").json()[
        "data"
    ]
    pid = project["id"]
    add_external_member(client, pid, "bob", by="alice")
    bob = session_auth_headers("bob")
    default = client.get(f"/projects/{pid}/agents", headers=bob).json()["data"]["data"][
        0
    ]
    url = f"/projects/{pid}/agents/{default['id']}"

    saved = client.put(
        url,
        json={"configuration": {"compact_percent": 60, "skills": ["documents"]}},
        headers=bob,
    )
    assert saved.status_code == 200, saved.text
    # A save that leaves them as they were says nothing more.
    renamed = client.put(
        url,
        json={
            "configuration": {
                "body": "Review",
                "compact_percent": 60,
                "skills": ["documents"],
            }
        },
        headers=bob,
    )
    assert renamed.status_code == 200, renamed.text
    made = client.post(
        f"/projects/{pid}/agents",
        json={"display_name": "x", "configuration": {"effort": "max"}},
        headers=bob,
    )
    assert made.status_code == 200, made.text

    blocks = client.get(
        f"/topics/{project['root_topic_id']}/blocks",
        headers=session_auth_headers("alice"),
    ).json()["data"]["data"]
    told = [
        (b["author"], b["meta"]["agent"], b["meta"]["setting"], b["meta"]["to"])
        for b in blocks
        if (b.get("meta") or {}).get("event_type") == "setting_changed"
    ]
    assert told == [
        ("bob", default["seat_handle"], "compact_percent", 60),
        ("bob", default["seat_handle"], "skills", ["documents"]),
    ]


#: Four room layouts that say the same thing about a person — bob is on the
#: project. They used to say four different things to the skills gate, which
#: read them out of the topic tree (``share_a_room_with``).
_ROOM_SHAPES = ("no_rooms", "no_root", "archived_only", "private_only")


@pytest.mark.parametrize("shape", _ROOM_SHAPES)
def test_arming_a_teammate_does_not_depend_on_rooms(
    api_client, db_session, _portal, shape
):
    """Arming a teammate is any member's, whatever shape the rooms take.

    Bob is a plain member of this project in all four layouts — the same person,
    the same roster — so arming a teammate must land in every one of them. The
    four shapes are exactly the layouts that used to read differently to the one
    gate that asked whether two handles shared a room (``share_a_room_with``):
    a project with no public room at all, one without 综合, one whose only rooms
    are archived, and one whose only room is a private chat.

    ``api_client`` and not ``client``: this test reshapes rooms through
    ``db_session``, and only the integration client shares that session.
    """
    # ``api_client`` 上没有人可替它注册（`_register` 只在拿得出会话的客户端上动手），
    # 所以这两个人要在这条测试共享的会话里自己建出来：邀请问的是「这个 handle 有
    # 人持有吗」，没有就是 422。
    _portal.call(registered, db_session, "alice")
    _portal.call(registered, db_session, "bob")
    made = post_project(api_client, json={"name": f"rooms-{shape}"}, owner="alice")
    project = made.json()["data"]
    pid = uuid.UUID(project["id"])
    add_external_member(api_client, project["id"], "bob", by="alice")
    bob = session_auth_headers("bob")
    agents = api_client.get(f"/projects/{pid}/agents", headers=bob)
    url = f"/projects/{pid}/agents/{agents.json()['data']['data'][0]['id']}"

    async def reshape() -> None:
        rows = list(
            await db_session.scalars(select(Topic).where(Topic.project_id == pid))
        )
        assert rows, "a new project starts with a room to reshape"
        for row in rows:
            if shape == "no_rooms":
                await db_session.delete(row)
            elif shape == "no_root":
                row.kind = TopicKind.topic
            elif shape == "archived_only":
                row.status = TopicStatus.archived
                row.archived_at = datetime.now(UTC)
            else:
                row.is_private = True
        await db_session.commit()

    _portal.call(reshape)

    armed = api_client.put(
        url, json={"configuration": {"skills": ["documents"]}}, headers=bob
    )
    assert armed.status_code == 200, armed.text
    assert armed.json()["data"]["configuration"]["skills"] == ["documents"]


class _FakeValkey:
    """Just enough of the client ``editing`` uses: get / set(nx, ex) / delete."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


def _first_agent(client, pid, headers) -> dict:
    return client.get(f"/projects/{pid}/agents", headers=headers).json()["data"][
        "data"
    ][0]


def test_a_stale_save_is_refused_and_says_the_version_that_won(client):
    """Two people editing one teammate: the second save is refused, not silent.

    The whole ``configuration`` is replaced at once, so last-write-wins used to
    drop the first edit without a word.
    """
    project = post_project(client, json={"name": "Version"}, owner="alice").json()[
        "data"
    ]
    pid = project["id"]
    alice = session_auth_headers("alice")
    first = _first_agent(client, pid, alice)
    url = f"/projects/{pid}/agents/{first['id']}"
    assert first["version"] == 1

    saved = client.put(
        url,
        json={"configuration": {"skills": ["documents"]}, "expected_version": 1},
        headers=alice,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["version"] == 2

    # The same read again: somebody wrote in between, so this one is refused and
    # told which version is current.
    stale = client.put(
        url,
        json={"configuration": {"skills": ["wolfram"]}, "expected_version": 1},
        headers=alice,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["data"]["version"] == 2
    # Nothing landed.
    assert _first_agent(client, pid, alice)["configuration"]["skills"] == ["documents"]

    # Without a version the save is unconditional, as it always was.
    loose = client.put(
        url, json={"configuration": {"skills": ["wolfram"]}}, headers=alice
    )
    assert loose.status_code == 200, loose.text
    assert loose.json()["data"]["version"] == 3


def test_the_version_moves_when_the_type_is_set_too():
    """Switching a teammate's type replaces its configuration, so it is a write
    like any other — a save that read the old version must not land on top."""
    import asyncio
    import uuid

    from app.domain.agent_instance.models import AgentInstance

    async def run():
        instance = AgentInstance(
            project_id=uuid.UUID(int=1),
            handle="t",
            display_name="T",
            configuration={},
            version=1,
        )
        # No session needed: ``set_type`` validates the preset against the
        # library and writes attributes.
        await AgentInstanceService(None).set_type(instance, "fullstack-engineer")
        assert instance.version == 2

    asyncio.run(run())


def test_every_teammate_is_in_综合_with_every_member(client):
    """What 「同一个话题的人」 resolves to for the project's own teammate.

    综合 IS the project: its members are everyone in it, an agent on the project
    roster included. So a member of the project shares that room with every
    teammate of it, and arming one is theirs to do without a manager — the same
    「在室成员可改」 the room's own skill toggles answer to. A member who is not
    in the project never gets this far: ``authorize_project`` refuses first.
    """
    project = post_project(client, json={"name": "Room"}, owner="alice").json()["data"]
    pid = project["id"]
    add_external_member(client, pid, "bob", by="alice")
    bob = session_auth_headers("bob")
    url = f"/projects/{pid}/agents/{_first_agent(client, pid, bob)['id']}"

    armed = client.put(
        url, json={"configuration": {"skills": ["documents"]}}, headers=bob
    )
    assert armed.status_code == 200, armed.text


def test_the_editing_lease_is_held_and_given_up(client, monkeypatch):
    """One person claims a teammate while they edit it; the second is told who
    has it and is refused the claim until it is given up."""
    from app.domain.agent_instance import editing

    fake = _FakeValkey()
    monkeypatch.setattr(editing, "get_redis_client", lambda: fake)
    project = post_project(client, json={"name": "Lease"}, owner="alice").json()["data"]
    pid = project["id"]
    add_external_member(client, pid, "bob", by="alice")
    alice, bob = session_auth_headers("alice"), session_auth_headers("bob")
    url = f"/projects/{pid}/agents/{_first_agent(client, pid, alice)['id']}/editing"

    assert client.put(url, json={"held": True}, headers=alice).status_code == 200
    # The other person's read says who is in it.
    assert _first_agent(client, pid, bob)["editing"] == "alice"
    # And they cannot take it.
    taken = client.put(url, json={"held": True}, headers=bob)
    assert taken.status_code == 409, taken.text
    assert taken.json()["error"]["data"]["handle"] == "alice"

    # Giving it up frees it for the next person.
    assert client.put(url, json={"held": False}, headers=alice).status_code == 200
    assert _first_agent(client, pid, bob)["editing"] is None
    assert client.put(url, json={"held": True}, headers=bob).status_code == 200


def test_without_valkey_nobody_is_editing_and_the_save_still_lands(client, monkeypatch):
    """The lease is a courtesy. With no Valkey it reads as nobody and never
    refuses: the version check is the answer that has to hold."""
    from app.domain.agent_instance import editing

    monkeypatch.setattr(editing, "get_redis_client", lambda: None)
    project = post_project(client, json={"name": "NoValkey"}, owner="alice").json()[
        "data"
    ]
    pid = project["id"]
    alice = session_auth_headers("alice")
    agent = _first_agent(client, pid, alice)
    url = f"/projects/{pid}/agents/{agent['id']}"

    assert (
        client.put(f"{url}/editing", json={"held": True}, headers=alice).status_code
        == 200
    )
    assert _first_agent(client, pid, alice)["editing"] is None
    assert (
        client.put(
            url,
            json={
                "configuration": {"skills": ["documents"]},
                "expected_version": agent["version"],
            },
            headers=alice,
        ).status_code
        == 200
    )
