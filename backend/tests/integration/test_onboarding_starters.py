"""Taking a new-user starter from the guide: the project is the 题目's, runs on
the starter model, and the platform's credits go out once per account."""

import uuid

from app.domain.onboarding.starters import STARTER_CREDITS, STARTER_MODEL


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(api_client, person) -> str:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": person.username, "password": person.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["accessToken"]


def _take(api_client, token, starter: str) -> dict:
    resp = api_client.post(
        "/users/me/onboarding",
        json={
            "project_id": str(uuid.uuid4()),
            "project_name": "不用这个名字",
            "intent": "",
            "work_role": "student",
            "starter": starter,
        },
        headers=_headers(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _grants(api_client, token, project_id: str) -> list[tuple[int, float]]:
    resp = api_client.get(f"/projects/{project_id}/credits", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    return [
        (g["source_task_id"], g["credits_total"]) for g in resp.json()["data"]["grants"]
    ]


def _project(api_client, token, project_id: str) -> dict:
    resp = api_client.get(f"/projects/{project_id}", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_taking_a_starter_makes_the_tasks_project_on_the_starter_model(
    api_client, user_client
):
    person = user_client.create_user()
    token = _token(api_client, person)
    done = _take(api_client, token, "web")

    assert done["root_topic_id"]
    project = _project(api_client, token, done["project_id"])
    assert project["external_task_id"]
    model = api_client.get(
        f"/projects/{done['project_id']}/default-model", headers=_headers(token)
    ).json()["data"]
    assert model["model"] == STARTER_MODEL
    assert _grants(api_client, token, done["project_id"]) == [
        (project["external_task_id"], float(STARTER_CREDITS))
    ]
    me = api_client.get("/users/me", headers=_headers(token)).json()["data"]["user"]
    assert me["onboarded_at"] and me["work_role"] == "student"


def test_taking_the_same_starter_again_opens_the_same_project(api_client, user_client):
    person = user_client.create_user()
    token = _token(api_client, person)
    first = _take(api_client, token, "slides")
    again = _take(api_client, token, "slides")
    assert again["project_id"] == first["project_id"]


def test_a_second_starter_gets_a_project_but_no_more_credits(api_client, user_client):
    person = user_client.create_user()
    token = _token(api_client, person)
    first = _take(api_client, token, "web")
    second = _take(api_client, token, "lesson")

    assert second["project_id"] != first["project_id"]
    assert len(_grants(api_client, token, first["project_id"])) == 1
    assert _grants(api_client, token, second["project_id"]) == []


def test_every_account_gets_its_own_starter_credits(api_client, user_client):
    one = user_client.create_user()
    two = user_client.create_user()
    token_one, token_two = _token(api_client, one), _token(api_client, two)
    a = _take(api_client, token_one, "web")
    b = _take(api_client, token_two, "web")

    assert a["project_id"] != b["project_id"]
    # Both on the same 题目 of the one board the first taker made.
    task_a = _project(api_client, token_one, a["project_id"])["external_task_id"]
    task_b = _project(api_client, token_two, b["project_id"])["external_task_id"]
    assert task_a == task_b
    assert len(_grants(api_client, token_two, b["project_id"])) == 1


def test_an_unknown_starter_is_refused(api_client, user_client):
    person = user_client.create_user()
    resp = api_client.post(
        "/users/me/onboarding",
        json={
            "project_id": str(uuid.uuid4()),
            "project_name": "x",
            "starter": "essay",
        },
        headers=_headers(_token(api_client, person)),
    )
    assert resp.status_code == 400
