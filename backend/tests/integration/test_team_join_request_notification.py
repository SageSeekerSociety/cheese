"""A join request waits in the team admins' 等你处理 until it is answered.

The row says who asked to join which team, so 待办 can offer approve and reject
on it; once answered it is no longer there. The push that called the admins back
still names both.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.push import push_text
from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def join_request(user_client: UserCreator, api_client: TestClient) -> dict:
    people = {}
    for role in ("owner", "requester", "member"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        people[role] = user
    name = f"Join Feed Team ({unique_int(10000000, 99999999)})"
    team = api_client.post(
        "/teams",
        json={
            "name": name,
            "intro": "",
            "description": "A team someone asks to join. " * 4,
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert team.status_code == 201, team.text
    team_id = team.json()["data"]["team"]["id"]
    asked = api_client.post(f"/teams/{team_id}/join", headers=auth(people["requester"]))
    assert asked.status_code in (200, 201), asked.text
    return {**people, "team_id": team_id, "team_name": name}


def waiting(api_client: TestClient, user) -> list[dict]:
    resp = api_client.get("/awaiting-me", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["data"]


def members(api_client: TestClient, join_request: dict) -> set[int]:
    resp = api_client.get(
        f"/teams/{join_request['team_id']}/members",
        headers=auth(join_request["owner"]),
    )
    return {m["user"]["id"] for m in resp.json()["data"]["members"]}


def test_a_pending_request_waits_on_the_teams_admins(api_client, join_request):
    [row] = waiting(api_client, join_request["owner"])

    assert row["kind"] == "join_request"
    assert row["placeId"] == str(join_request["team_id"])
    assert row["place"]["name"] == join_request["team_name"]
    assert row["person"]["handle"] == join_request["requester"].username


def test_the_requester_is_not_waited_on(api_client, join_request):
    assert waiting(api_client, join_request["requester"]) == []


def test_approving_it_lets_the_requester_in_and_it_stops_waiting(
    api_client, join_request
):
    [row] = waiting(api_client, join_request["owner"])

    approved = api_client.post(
        f"/teams/{row['placeId']}/requests/{row['id']}/approve",
        headers=auth(join_request["owner"]),
    )

    assert approved.status_code in (200, 204), approved.text
    assert join_request["requester"].user_id in members(api_client, join_request)
    assert waiting(api_client, join_request["owner"]) == []


def test_a_rejected_request_stops_waiting(api_client, join_request):
    [row] = waiting(api_client, join_request["owner"])

    rejected = api_client.post(
        f"/teams/{row['placeId']}/requests/{row['id']}/reject",
        headers=auth(join_request["owner"]),
    )

    assert rejected.status_code in (200, 204), rejected.text
    assert join_request["requester"].user_id not in members(api_client, join_request)
    assert waiting(api_client, join_request["owner"]) == []


def test_the_push_says_who_asked_to_join_which_team(
    api_client, join_request, db_session
):
    async def stored() -> Notification:
        row = await db_session.scalar(
            select(Notification).where(
                Notification.type == NotificationType.TEAM_JOIN_REQUEST,
                Notification.receiver_id == join_request["owner"].user_id,
            )
        )
        assert row is not None
        return row

    notice = api_client.portal.call(stored)

    title, _ = push_text(
        NotificationType.TEAM_JOIN_REQUEST, notice.metadata_payload or {}, "zh-CN"
    )

    assert join_request["team_name"] in title
    assert "申请加入" in title
