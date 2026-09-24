"""Agent-authored proposals retain independently resolved human credits."""

import uuid
from datetime import UTC, datetime

from app.domain.review.github_pr import OpenedPR
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import room_agent_seat


class _FakeTokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"


def test_delivery_credits_the_agent_actually_seated_in_the_room(client):
    from app.domain.repository import identity
    from app.domain.review.pr_text import pr_trailers
    from app.domain.topic.models import Topic
    from tests.integration.conftest import session_auth_headers

    pid, room = _project(client, owner="alice")
    default_seat = room_agent_seat(client, room)
    ops = client.post(f"/projects/{pid}/agents", json={"handle": "ops"})
    assert ops.status_code == 200, ops.text
    acting = ops.json()["data"]["seat_handle"]
    added = client.post(
        f"/topics/{room}/members",
        json={"handle": acting, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    removed = client.delete(
        f"/topics/{room}/members/{default_seat}?actor=alice",
        headers=session_auth_headers("alice"),
    )
    assert removed.status_code == 200, removed.text

    async def read_credit():
        async with client.test_request_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room))
            who = await identity.attribution(session, topic)
            return who, pr_trailers(topic, "alice", who)

    who, trailers = client.portal.call(lambda: read_credit())
    assert who.author == identity.agent_identity(acting)
    assert f"Cheese-Agent: {acting}\n" in trailers + "\n"
    assert f"Cheese-Agent: {default_seat}" not in trailers


def test_reporter_credit_survives_dispatch_and_only_declared_work_is_credited(client):
    from types import SimpleNamespace

    from app.domain.repository import identity
    from app.domain.room_task.place import PlaceResolver
    from app.domain.user.models import User

    async def seed():
        async with client.test_request_factory() as session:
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

    client.portal.call(lambda: seed())
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
        async with client.test_request_factory() as session:
            place = await PlaceResolver(session).resolve(uuid.UUID(room))
            assert place is not None
            card = SimpleNamespace(delivered_task_ids=[task["id"]], task_id=None)
            return await identity.attribution(session, place.room, card=card)

    credited = client.portal.call(lambda: read_credit())
    assert credited.reporters == (identity.platform_identity("reporter"),)
    assert credited.coauthors == (
        identity.platform_identity("alice"),
        identity.platform_identity("coder"),
    )
    assert credited.author == identity.agent_identity(room_agent_seat(client, room))
    concluded = client.post(
        f"/topics/{room}/tasks/{task['id']}/close",
        json={"contributor_handles": ["reporter"], "reporter_handle": None},
    )
    assert concluded.status_code == 200, concluded.text
    credited = client.portal.call(lambda: read_credit())
    assert credited.reporters == ()
    assert credited.coauthors == (
        identity.platform_identity("alice"),
        identity.platform_identity("reporter"),
    )
    preserved = client.post(f"/topics/{room}/tasks/{task['id']}/close", json={})
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()["data"]["contributor_handles"] == ["reporter"]
    rejected = client.post(
        f"/topics/{room}/tasks/{task['id']}/close",
        json={"contributor_handles": ["nobody-exists"]},
    )
    assert rejected.status_code == 422, rejected.text
    assert client.portal.call(lambda: read_credit()).coauthors == credited.coauthors
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

    async def update_pr(self, number: int, *, title: str, body: str) -> dict:
        return {"number": number, "title": title, "body": body}

    async def open_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> OpenedPR:
        type(self).opened.append({"body": body})
        return OpenedPR(
            {"number": 42, "html_url": "https://github.com/acme/widgets/pull/42"},
        )


def _github_world(monkeypatch, *, connected: dict[str, str]) -> None:
    """A GitHub the platform can push to, plus the set of handles that have
    actually connected an account (`connected[handle] -> their token`)."""
    from app.domain.repository import forge_files
    from app.domain.review import pr_publish

    _FakeClient.opened = []

    async def _fake_proposal_client(_project_id, _session):
        return _FakeClient("acme", "widgets", _FakeTokens())

    async def _fake_branch_head(_project_id, _session, _branch):
        return "a" * 40

    async def _comparison(_project_id, _session, _path):
        return {"total_commits": 1, "files": [], "commits": []}

    async def _fake_user_token(_session, handle: str) -> str | None:
        return connected.get(handle)

    monkeypatch.setattr(pr_publish, "proposal_client", _fake_proposal_client)
    monkeypatch.setattr(pr_publish, "branch_head", _fake_branch_head)
    monkeypatch.setattr(forge_files, "branch_head", _fake_branch_head)
    monkeypatch.setattr(forge_files, "repository_data", _comparison)
    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle", _fake_user_token
    )


def _project(client, owner: str) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    return p["id"], p["root_topic_id"]


def _split(client, parent_id: str, *, by: str) -> str:
    """Split as `by` would: no human token, the handle only in the body — the
    exact shape `cheese_task` sends from a 分身's sandbox."""
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

    client.portal.call(
        lambda: pr_publish._run(
            client.test_request_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )


def test_requester_oauth_does_not_change_the_pr_author(client, monkeypatch):
    """Requester OAuth never replaces the agent identity used to open a PR."""
    _github_world(monkeypatch, connected={"alice": "gho_alice"})

    pid, root = _project(client, owner="alice")
    agent = f"cheese-{uuid.uuid4().hex[:12]}"
    _split(client, root, by=agent)
    _publish(client, pid, root, _card(client, root))

    [opened] = _FakeClient.opened
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
    assert "Requested-by: alice" in opened["body"]
