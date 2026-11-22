"""问答域上的人按读的人自己的叫法，私人备注只进他自己的那一份。

提问、回答、邀请候选每一行都带一张作者卡（`profile_to_author`）：账号、公开昵称，
加上**这个查看者**给他设的个人备注。没设备注的人是 ``remark: None``，界面照旧画
公开昵称；别人的那一份永远读不到这条称呼。
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int

REMARK = "我们叫他老王"
PUBLIC = "出题人的公开昵称"


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def asked(
    user_client: UserCreator, api_client: TestClient, db_session, _portal
) -> dict:
    """一道题，出题人的昵称和账号不同（才分得清「换成了」和「本来就一样」）。"""
    asker = user_client.create_user(nickname=PUBLIC)
    asker.token = _login(user_client, api_client, asker)
    created = api_client.post(
        "/questions",
        headers=_auth(asker.token),
        json={
            "title": f"Remark question {unique_int(100000, 999999)}",
            "content": "问答域里的人名",
            "type": 0,
            "topics": [],
            "groupId": None,
            "bounty": 0,
        },
    )
    assert created.status_code == 201, created.text
    return {"asker": asker, "question_id": created.json()["data"]["id"]}


def _set_remark(api_client: TestClient, token: str, handle: str, remark: str) -> None:
    saved = api_client.put(
        f"/users/{handle}/remark", json={"remark": remark}, headers=_auth(token)
    )
    assert saved.status_code == 200, saved.text


def _reader(user_client: UserCreator, api_client: TestClient):
    reader = user_client.create_user(nickname="读者")
    reader.token = _login(user_client, api_client, reader)
    return reader


def test_the_question_list_names_its_author_by_the_readers_own_remark(
    user_client: UserCreator, api_client: TestClient, asked: dict
):
    reader = _reader(user_client, api_client)
    _set_remark(api_client, reader.token, asked["asker"].username, REMARK)

    resp = api_client.get(
        "/questions", params={"q": "Remark question"}, headers=_auth(reader.token)
    )
    assert resp.status_code == 200, resp.text
    row = next(
        q for q in resp.json()["data"]["questions"] if q["id"] == asked["question_id"]
    )
    assert row["author"]["remark"] == REMARK, row["author"]
    assert row["author"]["nickname"] == PUBLIC, row["author"]


def test_the_answer_list_names_its_author_by_the_readers_own_remark(
    user_client: UserCreator, api_client: TestClient, asked: dict
):
    answerer = user_client.create_user(nickname="回答人的公开昵称")
    answerer.token = _login(user_client, api_client, answerer)
    written = api_client.post(
        f"/questions/{asked['question_id']}/answers",
        headers=_auth(answerer.token),
        json={"content": "我的回答"},
    )
    assert written.status_code == 201, written.text

    reader = _reader(user_client, api_client)
    _set_remark(api_client, reader.token, answerer.username, REMARK)
    listed = api_client.get(
        f"/questions/{asked['question_id']}/answers", headers=_auth(reader.token)
    )
    assert listed.status_code == 200, listed.text
    row = listed.json()["data"]["answers"][0]
    assert row["author"]["remark"] == REMARK, row["author"]
    assert row["author"]["nickname"] == "回答人的公开昵称", row["author"]


def test_another_reader_keeps_the_public_name_and_sees_no_remark(
    user_client: UserCreator, api_client: TestClient, asked: dict
):
    answerer = user_client.create_user(nickname="回答人的公开昵称")
    answerer.token = _login(user_client, api_client, answerer)
    api_client.post(
        f"/questions/{asked['question_id']}/answers",
        headers=_auth(answerer.token),
        json={"content": "我的回答"},
    )

    one = _reader(user_client, api_client)
    _set_remark(api_client, one.token, answerer.username, REMARK)
    other = _reader(user_client, api_client)

    listed = api_client.get(
        f"/questions/{asked['question_id']}/answers", headers=_auth(other.token)
    )
    assert listed.status_code == 200, listed.text
    author = listed.json()["data"]["answers"][0]["author"]
    assert author["nickname"] == "回答人的公开昵称", author
    assert not author.get("remark"), author


def test_the_invitation_candidates_are_named_by_the_readers_own_remark(
    user_client: UserCreator, api_client: TestClient, asked: dict, db_session, _portal
):
    # `create_user` 已经建了带昵称的档案，不用再补一份。
    candidate = user_client.create_user(
        username="invitee-remark", nickname="候选人的公开昵称"
    )

    reader = _reader(user_client, api_client)
    _set_remark(api_client, reader.token, candidate.username, REMARK)

    resp = api_client.get(
        f"/questions/{asked['question_id']}/invitations/recommendations",
        params={"limit": 50},
        headers=_auth(reader.token),
    )
    assert resp.status_code == 200, resp.text
    row = next(u for u in resp.json()["data"]["users"] if u["id"] == candidate.user_id)
    assert row["remark"] == REMARK, row
    assert row["nickname"] == "候选人的公开昵称", row
    # 账号也带着：界面靠它认人、给它上色。
    assert row["username"] == candidate.username, row
