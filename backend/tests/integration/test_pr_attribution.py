"""分身开的 PR 认到人: a sub-topic split by a 分身 must still belong to a human.

The 分身 creates its sub-topics under its OWN handle (`cheese-<hex12>`), so
`Topic.created_by` there names a robot with no GitHub account. Everything that
asked `created_by` "who is this topic's human?" degraded at once: the PR opened
as `cheesex-app[bot]` (PR #504's `user.login`, measured), its body said
`Requested-by: cheese-a7a0268b96ff`, and the commits carried no
`Co-authored-by` at all. The roster knew — `split_to_subtopic` seeds a real
human as the child's owner — and that is what these read now.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.identity.handles import topic_agent_handle
from tests.delivery import delivery_headers, delivery_task_id


class _FakeTokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"


def test_delivery_credits_the_agent_actually_seated_in_the_room(client):
    from app.domain.review.pr_text import pr_trailers
    from app.domain.topic.models import Topic
    from app.domain.workspace import identity
    from tests.integration.conftest import session_auth_headers

    pid, room = _project(client, owner="alice")
    other = client.post(
        "/topics",
        json={"project_id": pid, "title": "Other agent", "created_by": "alice"},
    ).json()["data"]["id"]
    acting = topic_agent_handle(uuid.UUID(other))
    added = client.post(
        f"/topics/{room}/members",
        json={"handle": acting, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    removed = client.delete(
        f"/topics/{room}/members/{topic_agent_handle(uuid.UUID(room))}?actor=alice",
        headers=session_auth_headers("alice"),
    )
    assert removed.status_code == 200, removed.text

    async def read_credit():
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room))
            who = await identity.attribution(session, topic)
            return who, pr_trailers(topic, "alice", who)

    who, trailers = asyncio.run(read_credit())
    assert who.author == identity.agent_identity(acting)
    assert f"Cheese-Agent: {acting}\n" in trailers + "\n"
    assert f"Cheese-Agent: {topic_agent_handle(uuid.UUID(room))}" not in trailers


def test_reporter_credit_survives_dispatch_and_only_declared_work_is_credited(client):
    from types import SimpleNamespace

    from app.domain.room_task.place import PlaceResolver
    from app.domain.user.models import User
    from app.domain.workspace import identity

    async def seed():
        async with client.test_factory() as session:
            now = datetime.now(UTC)
            session.add_all(
                [
                    User(
                        username=name,
                        email=f"{name}@test.invalid",
                        created_at=now,
                        updated_at=now,
                    )
                    for name in ("reporter", "coder")
                ]
            )
            await session.commit()

    asyncio.run(seed())
    _, room = _project(client, owner="alice")
    result = client.post(
        f"/topics/{room}/split",
        json=dict(
            reviewer_handle="alice",
            **{
                "title": "Fix reported bug",
                "reporter_handle": "reporter",
                "contributor_handles": ["coder", "coder"],
            },
        ),
    )
    assert result.status_code == 200, result.text
    task = result.json()["data"]
    assert task["reporter_handle"] == "reporter"
    assert task["contributor_handles"] == ["coder"]

    async def read_credit():
        async with client.test_factory() as session:
            place = await PlaceResolver(session).resolve(uuid.UUID(room))
            assert place is not None
            card = SimpleNamespace(delivered_task_ids=[task["id"]], task_id=None)
            return await identity.attribution(session, place.room, card=card)

    credited = asyncio.run(read_credit())
    assert credited.reporters == (identity.platform_identity("reporter"),)
    assert credited.coauthors == (identity.platform_identity("coder"),)
    assert credited.author == identity.agent_identity(topic_agent_handle(room))
    concluded = client.post(
        f"/topics/{room}/tasks/{task['id']}/close",
        json={"contributor_handles": ["reporter"], "reporter_handle": None},
    )
    assert concluded.status_code == 200, concluded.text
    credited = asyncio.run(read_credit())
    assert credited.reporters == ()
    assert credited.coauthors == (identity.platform_identity("reporter"),)
    preserved = client.post(f"/topics/{room}/tasks/{task['id']}/close", json={})
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()["data"]["contributor_handles"] == ["reporter"]
    rejected = client.post(
        f"/topics/{room}/tasks/{task['id']}/close",
        json={"contributor_handles": ["nobody-exists"]},
    )
    assert rejected.status_code == 422, rejected.text
    assert asyncio.run(read_credit()).coauthors == credited.coauthors
    bad = client.post(
        f"/topics/{room}/split",
        json=dict(
            reviewer_handle="alice",
            **{"title": "bad", "reporter_handle": "nobody-exists"},
        ),
    )
    assert bad.status_code == 422, bad.text


class _FakeClient:
    opened: list[dict] = []

    def __init__(self, owner: str, repo: str, tokens, **_):
        pass

    async def open_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        as_user_token: str | None = None,
    ) -> dict:
        type(self).opened.append({"body": body, "as_user_token": as_user_token})
        return {"number": 42, "html_url": "https://github.com/acme/widgets/pull/42"}


def _github_world(monkeypatch, *, connected: dict[str, str]) -> None:
    """A GitHub the platform can push to, plus the set of handles that have
    actually connected an account (`connected[handle] -> their token`)."""
    from app.domain.review import pr_publish
    from app.domain.workspace import service as ws

    _FakeClient.opened = []

    async def _fake_tokens_for_project(_project_id, _session):
        return _FakeTokens()

    async def _fake_user_token(_session, handle: str) -> str | None:
        return connected.get(handle)

    monkeypatch.setattr(
        pr_publish, "github_app_tokens_for_project", _fake_tokens_for_project
    )
    monkeypatch.setattr(pr_publish, "GitHubPRClient", _FakeClient)
    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle", _fake_user_token
    )
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )
    monkeypatch.setattr(
        ws, "push_topic_branch", lambda pid, tid, token: f"topic/{tid.hex[:8]}"
    )
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid, tid: True)
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo, **_: "main")


def _project(client, owner: str) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    return p["id"], p["root_topic_id"]


def _split(client, parent_id: str, *, by: str) -> str:
    """Split as `by` would: no human token, the handle only in the body — the
    exact shape `cheese split` sends from a 分身's sandbox."""
    r = client.post(
        f"/topics/{parent_id}/split",
        json=dict(
            reviewer_handle="alice", **{"title": "分身拆出的子任务", "created_by": by}
        ),
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _card(client, topic_id: str) -> str:
    r = client.post(
        f"/topics/{topic_id}/tasks/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
            "change_subject": "fix(accept): credit the human, not the bot",
        },
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _publish(client, pid: str, tid: str, cid: str) -> None:
    from app.domain.review import pr_publish

    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )


def test_work_an_agent_split_out_delivers_as_the_human_who_owns_the_room(
    client, monkeypatch
):
    """验收 1+2: alice owns the room and has connected GitHub, so the PR is
    opened with HER token (not the App's, which is what makes the author
    `cheesex-app[bot]`) and its body names her.

    递卡是房间的事，所以卡从 root 递：分身拆出去的活和它兄弟们的活在同一条分支上，
    一个 PR 一起交付。**哪一条支线是谁推进的，PR 上看不出来**——单一的
    `Requested-by:` 装不下一整棵树的人，这是 #615「一个 PR 横跨多条支线」之后就
    已经存在的事，「只有房间能递卡」只是让它显形。
    """
    _github_world(monkeypatch, connected={"alice": "gho_alice"})

    pid, root = _project(client, owner="alice")
    agent = f"cheese-{uuid.uuid4().hex[:12]}"
    _split(client, root, by=agent)
    _publish(client, pid, root, _card(client, root))

    [opened] = _FakeClient.opened
    assert opened["as_user_token"] == "gho_alice"
    assert "Requested-by: alice" in opened["body"]
    assert agent not in opened["body"]


def test_the_agent_handle_never_reaches_the_pr_even_when_nobody_connected_github(
    client, monkeypatch
):
    """验收 4: alice owns the room but has no GitHub connection. The PR still
    opens — with the App token, as before — and still credits her by handle."""
    _github_world(monkeypatch, connected={})

    pid, root = _project(client, owner="alice")
    agent = f"cheese-{uuid.uuid4().hex[:12]}"
    _split(client, root, by=agent)
    _publish(client, pid, root, _card(client, root))

    [opened] = _FakeClient.opened
    assert opened["as_user_token"] is None
    assert "Requested-by: alice" in opened["body"]
    assert agent not in opened["body"]


def test_a_room_with_no_human_owner_still_opens_its_pr(client, monkeypatch):
    """验收 4: an ownerless project has an ownerless room. Nothing to resolve —
    the PR must open anyway rather than raising, and the 分身 handle must not be
    what fills the gap."""
    _github_world(monkeypatch, connected={"alice": "gho_alice"})

    p = client.post("/projects", json={"name": "P"}).json()["data"]
    pid, root = p["id"], p["root_topic_id"]
    agent = f"cheese-{uuid.uuid4().hex[:12]}"
    _split(client, root, by=agent)
    _publish(client, pid, root, _card(client, root))

    [opened] = _FakeClient.opened
    assert opened["as_user_token"] is None
    assert agent not in opened["body"]


def test_a_card_cannot_open_a_pr_of_its_own(client, monkeypatch):
    """上面三条都从房间递卡，是因为一张卡递不了——这条钉住那个前提。

    走不通的方式是 404：卡不是地点，那个 id 名下没有话题可以递。
    """
    _github_world(monkeypatch, connected={"alice": "gho_alice"})

    _, root = _project(client, owner="alice")
    tid = _split(client, root, by=f"cheese-{uuid.uuid4().hex[:12]}")

    r = client.post(
        f"/topics/{tid}/tasks/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
            "change_subject": "fix(accept): credit the human, not the bot",
        },
    )
    assert r.status_code == 404, r.text
    assert _FakeClient.opened == []


def test_a_topic_a_human_opened_directly_is_untouched(client, monkeypatch):
    """验收 5: creator and owner are the same person, so the answer must be the
    one this path always gave."""
    _github_world(monkeypatch, connected={"alice": "gho_alice"})

    pid, _ = _project(client, owner="alice")
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "做一个东西", "created_by": "alice"},
    )
    tid = r.json()["data"]["id"]
    _publish(client, pid, tid, _card(client, tid))

    [opened] = _FakeClient.opened
    assert opened["as_user_token"] == "gho_alice"
    assert "Requested-by: alice" in opened["body"]
