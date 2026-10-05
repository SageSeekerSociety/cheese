"""Turning someone away from a team is said in the reader's language.

Inviting a person who is already in the team, inviting one whose invitation or
join request is still waiting, and adding an existing member directly are all
refused. The refusal reaches a Chinese screen in Chinese and an English screen
in English: ``error.message`` is the Chinese sentence, and ``error.i18n`` names
the catalog sentence the screen renders in its reader's language.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.sentences import render
from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def team(user_client: UserCreator, api_client: TestClient) -> dict:
    people = {}
    for role in ("owner", "member", "outsider"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        people[role] = user
    created = api_client.post(
        "/teams",
        json={
            "name": f"Refusal Team ({unique_int(10000000, 99999999)})",
            "intro": "",
            "description": "A team that turns people away. " * 4,
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["data"]["team"]["id"]
    added = api_client.post(
        f"/teams/{team_id}/members",
        json={"userId": people["member"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert added.status_code == 201, added.text
    return {**people, "team_id": team_id}


def invite(api_client: TestClient, team: dict, who) -> object:
    return api_client.post(
        f"/teams/{team['team_id']}/invitations",
        json={"userId": who.user_id, "role": "MEMBER"},
        headers=auth(team["owner"]),
    )


def test_inviting_a_member_is_refused_in_chinese_and_english(api_client, team):
    r = invite(api_client, team, team["member"])

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["message"] == "这个人已经是这个团队的成员了"
    assert render(error["i18n"], "zh-CN") == "这个人已经是这个团队的成员了"
    assert render(error["i18n"], "en") == "This person is already a member of this team"


def test_inviting_someone_twice_is_refused_in_chinese_and_english(api_client, team):
    first = invite(api_client, team, team["outsider"])
    assert first.status_code == 201, first.text

    r = invite(api_client, team, team["outsider"])

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["message"] == "这个人已经有一条加入这个团队的邀请或申请在等处理"
    assert render(error["i18n"], "zh-CN") == error["message"]
    assert render(error["i18n"], "en") == (
        "This person already has an invitation or join request for this team "
        "waiting to be answered"
    )


def test_adding_a_member_again_is_refused_in_chinese_and_english(api_client, team):
    r = api_client.post(
        f"/teams/{team['team_id']}/members",
        json={"userId": team["member"].user_id, "role": "MEMBER"},
        headers=auth(team["owner"]),
    )

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["message"] == "这个人已经是这个团队的成员了"
    assert render(error["i18n"], "en") == "This person is already a member of this team"
