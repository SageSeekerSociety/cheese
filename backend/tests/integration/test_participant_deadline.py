"""A participant's deadline is their own claim's, on their own clock.

A challenge has a closing time; a person who claims it gets their own deadline
when the publisher approves the claim (approval time plus the 提交期限), and the
publisher may move it afterwards. Three readers have to name the same moment:

1. the challenge page (``GET /tasks/{id}``'s ``userDeadline``) gives the
   deadline of the claim the person actually stands on — an approved one, not
   a rejected one that happens to come first;
2. what a person's 芝士 reads about their challenges (``GET /tasks/joined``)
   carries that same deadline, and every time is on the person's own clock
   (the time zone their browser reported), offset included;
3. the project a claim opens carries that claim's deadline live
   (``GET /projects/{id}``), and has none written into its overview text at
   claim time — the claim is not approved yet then, so no deadline of the
   person's exists to write, and one fixed string cannot be on every reader's
   clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)

#: Not the server's zone, not UTC, and nine hours from the platform default:
#: a time rendered on any other clock lands on another day or hour here.
ZONE = "America/Los_Angeles"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _data(resp):
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    return body["data"] if isinstance(body, dict) and "data" in body else body


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Deadline board ({unique_int()})",
            "intro": "一块板",
            "description": "截止时间",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "token": token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _member(
    user_client: UserCreator, api_client: TestClient, board: dict
) -> tuple[CreatedUser, str]:
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 201, resp.text
    resp = api_client.put(
        "/users/me/timezone", json={"timezone": ZONE}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return user, token


def _task(
    api_client: TestClient, board: dict, *, submitter_type: str, closes: datetime
) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"题 {unique_int()}",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": submitter_type,
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 14,
            "deadline": _ms(closes),
        },
        headers=_auth(board["token"]),
    )
    task_id = _data(resp)["task"]["id"]
    _data(
        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers=_auth(board["token"]),
        )
    )
    return task_id


def _decide(
    api_client: TestClient, board: dict, task_id: int, participant_id: int, **body
):
    _data(
        api_client.patch(
            f"/tasks/{task_id}/participants/{participant_id}",
            json=body,
            headers=_auth(board["token"]),
        )
    )


def _joined(api_client: TestClient, token: str, task_id: int) -> dict:
    rows = _data(api_client.get("/tasks/joined", headers=_auth(token)))
    return next(row for row in rows if row["id"] == task_id)


def _same_moment_on_my_clock(iso: str, moment: datetime) -> None:
    said = datetime.fromisoformat(iso)
    assert said == moment
    assert said.utcoffset() == moment.astimezone(ZoneInfo(ZONE)).utcoffset()


def test_my_deadline_is_my_approved_claims_on_my_own_clock(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    closes = datetime(2030, 10, 25, 6, 59, tzinfo=UTC)
    mine = datetime(2030, 10, 24, 8, 30, tzinfo=UTC)
    _, token = _member(user_client, api_client, board)
    task_id = _task(api_client, board, submitter_type="USER", closes=closes)
    claimed = _data(
        api_client.post(
            f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
        )
    )

    # Waiting for approval: the challenge closes at its own time; no deadline
    # of mine exists yet.
    row = _joined(api_client, token, task_id)
    _same_moment_on_my_clock(row["deadline"], closes)
    assert row["myDeadline"] is None and row["approved"] is False

    _decide(
        api_client,
        board,
        task_id,
        claimed["participant"]["id"],
        approved="APPROVED",
        deadline=_ms(mine),
    )

    row = _joined(api_client, token, task_id)
    _same_moment_on_my_clock(row["myDeadline"], mine)
    _same_moment_on_my_clock(row["deadline"], closes)
    assert row["approved"] is True
    page = _data(api_client.get(f"/tasks/{task_id}", headers=_auth(token)))
    assert page["task"]["userDeadline"] == _ms(mine)


def test_a_team_task_names_the_deadline_of_the_claim_that_was_approved(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    """Rejected first, approved second: the deadline is the approved claim's."""
    _, token = _member(user_client, api_client, board)
    teams = [
        _data(
            api_client.post(
                "/teams",
                json={
                    "name": f"队 {unique_int()}",
                    "intro": "队",
                    "description": "队",
                    "avatarId": 1,
                },
                headers=_auth(token),
            )
        )["team"]["id"]
        for _ in range(2)
    ]
    task_id = _task(
        api_client,
        board,
        submitter_type="TEAM",
        closes=datetime.now(UTC) + timedelta(days=30),
    )
    first = _data(
        api_client.post(
            f"/tasks/{task_id}/participations/team",
            json={"teamId": teams[0]},
            headers=_auth(token),
        )
    )
    _decide(
        api_client,
        board,
        task_id,
        first["participant"]["id"],
        approved="DISAPPROVED",
    )
    second = _data(
        api_client.post(
            f"/tasks/{task_id}/participations/team",
            json={"teamId": teams[1]},
            headers=_auth(token),
        )
    )
    mine = datetime.now(UTC).replace(microsecond=0) + timedelta(days=14)
    _decide(
        api_client,
        board,
        task_id,
        second["participant"]["id"],
        approved="APPROVED",
        deadline=_ms(mine),
    )

    page = _data(api_client.get(f"/tasks/{task_id}", headers=_auth(token)))
    assert page["task"]["userDeadline"] == _ms(mine)
    _same_moment_on_my_clock(_joined(api_client, token, task_id)["myDeadline"], mine)
    project = _data(
        api_client.get(f"/projects/{second['project']['id']}", headers=_auth(token))
    )
    assert project["challenge_deadline"]["mine"] is True
    assert datetime.fromisoformat(project["challenge_deadline"]["at"]) == mine


def test_the_claims_project_carries_its_live_deadline_not_a_written_one(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    _, token = _member(user_client, api_client, board)
    closes = datetime(2030, 10, 25, 6, 59, tzinfo=UTC)
    task_id = _task(api_client, board, submitter_type="USER", closes=closes)
    claimed = _data(
        api_client.post(
            f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
        )
    )
    project_id = claimed["project"]["id"]

    # What the overview page shows instead, live: before approval the
    # challenge's closing time, after it the claim's own deadline.
    project = _data(api_client.get(f"/projects/{project_id}", headers=_auth(token)))
    assert project["challenge_deadline"]["mine"] is False
    assert datetime.fromisoformat(project["challenge_deadline"]["at"]) == closes
    mine = datetime(2030, 10, 24, 8, 30, tzinfo=UTC)
    _decide(
        api_client,
        board,
        task_id,
        claimed["participant"]["id"],
        approved="APPROVED",
        deadline=_ms(mine),
    )
    project = _data(api_client.get(f"/projects/{project_id}", headers=_auth(token)))
    assert project["challenge_deadline"]["mine"] is True
    assert datetime.fromisoformat(project["challenge_deadline"]["at"]) == mine

    overview = _data(
        api_client.get(f"/projects/{project_id}/overview", headers=_auth(token))
    )
    text = _data(api_client.get(f"/documents/{overview['id']}", headers=_auth(token)))
    content = text["content"] if isinstance(text, dict) else text
    # The task's requirements are carried over; a deadline is not.
    assert f"/tasks/{task_id}" in content
    assert "截止" not in content and "2030" not in content
