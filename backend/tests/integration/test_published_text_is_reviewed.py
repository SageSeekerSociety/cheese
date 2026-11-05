"""A write that would publish refused content leaves nothing published.

Product rule (知是可见性与加入规则): text and pictures every signed-in user can
see pass Content Moderation when they are written — a profile's nickname,
introduction and avatar, a recruitment post, a discoverable team's name and
introduction, and the problems and announcements of an open space. A refused
verdict refuses the write and nothing of it is stored. Content only members
can see — a hidden team, a members-only space — is not reviewed.

The service is the double in ``tests/support/content_review.py``; any text
containing ``BANNED`` is high risk.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.domain.user.models import User
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    session_auth_headers,
    unique_int,
)
from tests.integration.test_account_uniqueness import _oauth_create, _registration
from tests.integration.test_avatars import isolate_avatar_storage
from tests.support.content_review import Green

BANNED = "BANNED"
REVIEWER = "content-review-reviewer"


@pytest.fixture
def refusing(green: Green) -> Green:
    green.risk[BANNED] = "high"
    return green


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signed_in(api_client: TestClient, user_client: UserCreator, domain="x.org"):
    user = user_client.create_user(email=f"p-{uuid.uuid4().hex[:12]}@{domain}")
    return user, user_client.login(api_client, user.username, user.password)


# --- profile ---------------------------------------------------------------


def test_a_refused_nickname_or_intro_leaves_the_profile_as_it_was(
    api_client: TestClient, user_client: UserCreator, refusing: Green
):
    user, token = _signed_in(api_client, user_client)
    before = api_client.get(f"/users/{user.user_id}", headers=_auth(token))
    assert before.status_code == 200, before.text

    for field in ("nickname", "intro"):
        resp = api_client.patch(
            f"/users/{user.user_id}",
            json={field: f"{BANNED} words"},
            headers=_auth(token),
        )
        assert resp.status_code == 422, resp.text

    after = api_client.get(f"/users/{user.user_id}", headers=_auth(token))
    assert after.json()["data"]["user"] == before.json()["data"]["user"]

    resp = api_client.patch(
        f"/users/{user.user_id}", json={"nickname": "小明"}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text


async def test_a_refused_nickname_creates_no_account(client, refusing: Green):
    suffix = uuid.uuid4().hex[:8]
    body = await _registration(f"rv_{suffix}", f"rv-{suffix}@example.com")
    body["nickname"] = f"{BANNED} name"

    resp = client.post("/users", json=body)

    assert resp.status_code == 422, resp.text
    assert _accounts(client, f"rv_{suffix}") == 0


async def test_a_refused_nickname_creates_no_account_through_a_provider(
    client, refusing: Green
):
    username = f"rvo_{uuid.uuid4().hex[:8]}"
    params = _oauth_create(
        client,
        provider_uid=f"review-{uuid.uuid4().hex[:8]}",
        username=username,
        nickname=f"{BANNED} name",
    )

    assert params["error_code"] == "INVALID_NICKNAME"
    assert _accounts(client, username) == 0


def _accounts(client, username: str) -> int:
    async def count() -> int:
        async with client.test_request_factory() as session:
            return await session.scalar(
                select(func.count(User.id)).where(User.username == username)
            )

    return client.portal.call(count)


def test_a_refused_picture_is_not_stored(
    api_client: TestClient,
    user_client: UserCreator,
    green: Green,
    monkeypatch,
    tmp_path,
):
    storage = isolate_avatar_storage(monkeypatch, tmp_path)
    green.image_risk = "high"
    _, token = _signed_in(api_client, user_client)

    resp = api_client.post(
        "/avatars",
        files={"avatar": ("me.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, "image/png")},
        headers=_auth(token),
    )

    assert resp.status_code == 422, resp.text
    assert not storage.exists() or not any(storage.iterdir())


# --- teams and recruitment -------------------------------------------------


def _team(api_client: TestClient, token: str) -> int:
    resp = api_client.post(
        "/teams",
        json={
            "name": f"Review team ({unique_int()})",
            "intro": "我们做系统",
            "description": "desc",
            "avatarId": 1,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["team"]["id"]


def _read_team(api_client: TestClient, token: str, team_id: int) -> dict:
    resp = api_client.get(f"/teams/{team_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["team"]


def test_a_refused_recruitment_post_is_not_posted_or_changed(
    api_client: TestClient, user_client: UserCreator, refusing: Green
):
    _, token = _signed_in(api_client, user_client)
    team_id = _team(api_client, token)

    for body in (
        {"title": BANNED, "content": "x"},
        {"title": "x", "content": BANNED},
        {"title": "x", "content": "x", "contact": f"{BANNED} 微信"},
    ):
        resp = api_client.post(
            f"/teams/{team_id}/recruitment", json=body, headers=_auth(token)
        )
        assert resp.status_code == 422, resp.text
    listed = api_client.get(f"/teams/{team_id}/recruitment", headers=_auth(token))
    assert listed.json()["data"]["posts"] == []

    resp = api_client.post(
        f"/teams/{team_id}/recruitment",
        json={"title": "招后端", "content": "做调度器"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    post_id = resp.json()["data"]["post"]["id"]
    resp = api_client.patch(
        f"/recruitment/{post_id}",
        json={"content": f"{BANNED} now"},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text
    listed = api_client.get(f"/teams/{team_id}/recruitment", headers=_auth(token))
    assert [p["content"] for p in listed.json()["data"]["posts"]] == ["做调度器"]


def test_a_discoverable_team_cannot_be_named_or_introduced_with_refused_words(
    api_client: TestClient, user_client: UserCreator, refusing: Green
):
    _, token = _signed_in(api_client, user_client)
    resp = api_client.post(
        "/teams",
        json={"name": f"{BANNED} {unique_int()}", "intro": "", "avatarId": 1},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text

    team_id = _team(api_client, token)
    before = _read_team(api_client, token, team_id)
    for body in ({"name": f"{BANNED} team"}, {"intro": BANNED}):
        resp = api_client.patch(f"/teams/{team_id}", json=body, headers=_auth(token))
        assert resp.status_code == 422, resp.text
    after = _read_team(api_client, token, team_id)
    assert (after["name"], after["intro"]) == (before["name"], before["intro"])


def test_a_hidden_team_says_what_it_likes_until_it_becomes_discoverable(
    api_client: TestClient, user_client: UserCreator, refusing: Green
):
    _, token = _signed_in(api_client, user_client)
    team_id = _team(api_client, token)
    for body in ({"visibility": "stealth"}, {"intro": f"{BANNED} inside"}):
        resp = api_client.patch(f"/teams/{team_id}", json=body, headers=_auth(token))
        assert resp.status_code == 200, resp.text

    resp = api_client.patch(
        f"/teams/{team_id}", json={"visibility": "public"}, headers=_auth(token)
    )

    assert resp.status_code == 422, resp.text
    assert _read_team(api_client, token, team_id)["visibility"] == "stealth"


# --- open spaces -----------------------------------------------------------


def _space(api_client: TestClient, token: str) -> int:
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Review space ({unique_int()})",
            "intro": "",
            "description": "",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["space"]["id"]


def _open(api_client: TestClient, token: str, space_id: int, monkeypatch) -> None:
    """Open the space to ``x.org``, through the platform's review."""
    monkeypatch.setattr(settings, "platform_admin_handles", [REVIEWER])
    resp = api_client.put(
        f"/spaces/{space_id}/access",
        json={"mode": "open", "scope": ["x.org"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    resp = api_client.post(
        f"/admin/spaces/{space_id}/review",
        json={"approved": True, "reason": "", "openScope": ["x.org"]},
        headers=session_auth_headers(REVIEWER),
    )
    assert resp.status_code == 200, resp.text


def _problem(name: str, space_id: int, description: str = "") -> dict:
    return {
        "name": name,
        "intro": "题",
        "description": description or '{"type":"doc","content":[]}',
        "space": space_id,
        "submitterType": "USER",
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
    }


def _rich(text: str) -> str:
    return (
        '{"type":"doc","content":[{"type":"paragraph","content":'
        f'[{{"type":"text","text":"{text}"}}]}}]}}'
    )


def test_an_open_spaces_problem_cannot_say_refused_words(
    api_client: TestClient, user_client: UserCreator, refusing: Green, monkeypatch
):
    _, token = _signed_in(api_client, user_client)
    space_id = _space(api_client, token)
    _open(api_client, token, space_id, monkeypatch)

    for body in (
        _problem(f"{BANNED} 实验", space_id),
        _problem("实验", space_id, _rich(f"说明 {BANNED}")),
    ):
        resp = api_client.post("/tasks", json=body, headers=_auth(token))
        assert resp.status_code == 422, resp.text

    resp = api_client.post(
        "/tasks", json=_problem("实验", space_id), headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"description": _rich(BANNED)},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text
    read = api_client.get(f"/tasks/{task_id}", headers=_auth(token))
    assert read.json()["data"]["task"]["description"] == '{"type":"doc","content":[]}'


def test_an_open_spaces_announcement_cannot_say_refused_words(
    api_client: TestClient, user_client: UserCreator, refusing: Green, monkeypatch
):
    _, token = _signed_in(api_client, user_client)
    space_id = _space(api_client, token)
    _open(api_client, token, space_id, monkeypatch)
    url = f"/spaces/{space_id}/announcements"

    for body in (
        {"title": BANNED, "content": "<p>x</p>"},
        {"title": "通知", "content": f"<p>{BANNED}</p>"},
    ):
        resp = api_client.post(url, json=body, headers=_auth(token))
        assert resp.status_code == 422, resp.text

    resp = api_client.post(
        url, json={"title": "通知", "content": "<p>周五交</p>"}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    announcement_id = resp.json()["data"]["announcement"]["id"]
    resp = api_client.patch(
        f"{url}/{announcement_id}",
        json={"content": f"<p>{BANNED}</p>"},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text
    listed = api_client.get(url, headers=_auth(token)).json()["data"]
    assert [a["title"] for a in listed["current"]] == ["通知"]
    assert "周五交" in listed["current"][0]["content"]


def test_a_members_only_space_is_not_reviewed(
    api_client: TestClient, user_client: UserCreator, green: Green
):
    # Even an unreachable service does not matter where nothing is published.
    green.down = True
    _, token = _signed_in(api_client, user_client)
    space_id = _space(api_client, token)

    resp = api_client.post(
        "/tasks", json=_problem("课内实验", space_id), headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    resp = api_client.post(
        f"/spaces/{space_id}/announcements",
        json={"title": "课内通知", "content": "<p>x</p>"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
