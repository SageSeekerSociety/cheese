"""The new-user guide: its answers become the person's project and what every AI
teammate knows about them; the guide in the project reads what has really
happened."""

import uuid
from datetime import UTC, datetime

from app.domain.agent.models import AgentTurn
from app.domain.agent.turn.intake.rooms import room_roster
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.topic.models import Topic


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(api_client, person) -> str:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": person.username, "password": person.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["accessToken"]


def _finish(api_client, token, **body) -> dict:
    answers = {
        "project_id": str(uuid.uuid4()),
        "project_name": "数据库课程报告",
        "intent": "《数据库系统》课程报告：比较两种索引结构",
        "work_role": "student",
        "organization": "中国人民大学",
        **body,
    }
    resp = api_client.post(
        "/users/me/onboarding", json=answers, headers=_headers(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _me(api_client, token) -> dict:
    return api_client.get("/users/me", headers=_headers(token)).json()["data"]["user"]


def _projects(api_client, token) -> list[dict]:
    return api_client.get("/projects", headers=_headers(token)).json()["data"]["data"]


def test_a_new_account_has_not_been_through_the_guide(api_client, user_client):
    person = user_client.create_user()
    me = _me(api_client, _token(api_client, person))
    assert me["onboarded_at"] is None
    assert me["work_role"] is None


def test_finishing_the_guide_makes_the_project_and_keeps_the_answers(
    api_client, user_client
):
    person = user_client.create_user()
    token = _token(api_client, person)
    done = _finish(api_client, token, nickname="林小满")

    assert [(p["id"], p["name"]) for p in _projects(api_client, token)] == [
        (done["project_id"], "数据库课程报告")
    ]
    assert done["root_topic_id"]
    me = _me(api_client, token)
    assert me["onboarded_at"]
    assert (me["work_role"], me["organization"]) == ("student", "中国人民大学")
    assert me["nickname"] == "林小满"


def test_someone_working_alone_reviews_their_own_teammates_work(
    api_client, user_client
):
    person = user_client.create_user()
    token = _token(api_client, person)
    done = _finish(api_client, token, solo=True)
    rules = api_client.get(
        f"/projects/{done['project_id']}/branch-protection", headers=_headers(token)
    ).json()["data"]
    assert rules["default_reviewer"] == person.username


def test_a_retry_after_a_lost_answer_makes_no_second_project(api_client, user_client):
    person = user_client.create_user()
    token = _token(api_client, person)
    same = str(uuid.uuid4())
    _finish(api_client, token, project_id=same)
    _finish(api_client, token, project_id=same)
    assert len(_projects(api_client, token)) == 1


def test_people_named_are_invited_and_unknown_names_are_reported(
    api_client, user_client
):
    person, friend = user_client.create_user(), user_client.create_user()
    token = _token(api_client, person)
    done = _finish(
        api_client,
        token,
        solo=False,
        invitees=[friend.username, "nobody-by-this-name"],
    )
    assert done["not_invited"] == ["nobody-by-this-name"]
    invited = api_client.get(
        f"/projects/{done['project_id']}/invitations", headers=_headers(token)
    )
    assert invited.status_code == 200, invited.text
    assert friend.username in invited.text


def test_every_ai_teammate_reads_who_the_person_said_they_are(
    api_client, user_client, db_session, _portal
):
    person = user_client.create_user()
    token = _token(api_client, person)
    done = _finish(api_client, token)

    async def roster():
        topic = await db_session.get(Topic, uuid.UUID(done["root_topic_id"]))
        return await room_roster(db_session, topic.project_id, topic)

    rows = _portal.call(roster)
    mine = next(r for r in rows if r["handle"] == person.username)
    assert mine["about"] == "学生，中国人民大学"


def _computer(db_session, _portal, owner_id: int) -> str:
    async def enroll() -> str:
        devices = sql_device_service(db_session)
        device = await devices.approve(
            await devices.start("laptop"),
            owner_user_id=owner_id,
            supply=Supply.self_hosted,
        )
        await db_session.flush()
        return device.device_id

    return _portal.call(enroll)


def _room_default(api_client, token, room) -> dict | None:
    resp = api_client.get(f"/topics/{room}/environment", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["default"]


def test_from_the_desktop_app_the_teammate_works_on_this_computer(
    api_client, user_client, db_session, _portal
):
    person = user_client.create_user()
    token = _token(api_client, person)
    computer = _computer(db_session, _portal, person.user_id)
    done = _finish(api_client, token, device_id=computer)
    assert done["on_device"] is True
    assert _room_default(api_client, token, done["root_topic_id"]) == {
        "kind": "sandbox",
        "spec": f"devices_of:{person.user_id}",
    }


def test_someone_elses_computer_is_not_used_and_the_project_is_made_anyway(
    api_client, user_client, db_session, _portal
):
    person, other = user_client.create_user(), user_client.create_user()
    token = _token(api_client, person)
    computer = _computer(db_session, _portal, other.user_id)
    done = _finish(api_client, token, device_id=computer)
    assert done["on_device"] is False
    assert _room_default(api_client, token, done["root_topic_id"]) is None
    assert done["project_id"] in [p["id"] for p in _projects(api_client, token)]


def test_start_steps_follow_what_happened(api_client, user_client, db_session, _portal):
    person = user_client.create_user()
    token = _token(api_client, person)
    done = _finish(api_client, token)
    project, room = done["project_id"], done["root_topic_id"]

    def steps() -> dict:
        resp = api_client.get(
            f"/projects/{project}/start-steps", headers=_headers(token)
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    assert steps() == {
        "asked": False,
        "answered": False,
        "accepted": False,
        "learned": False,
    }

    # A turn ran on their message: they handed 芝士 something.
    async def asked():
        turn = uuid.uuid4()
        db_session.add(
            AgentTurn(
                id=turn,
                conversation_id=uuid.UUID(room),
                continuation_id=turn,
                author=person.username,
                content="@芝士 帮我列提纲",
                started_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

    _portal.call(asked)
    assert steps()["asked"] is True
    assert steps()["answered"] is False


def test_start_steps_are_not_told_to_outsiders(api_client, user_client):
    person, stranger = user_client.create_user(), user_client.create_user()
    done = _finish(api_client, _token(api_client, person))
    resp = api_client.get(
        f"/projects/{done['project_id']}/start-steps",
        headers=_headers(_token(api_client, stranger)),
    )
    assert resp.status_code in (403, 404), resp.text


def test_signed_out_requests_are_refused(api_client):
    resp = api_client.post(
        "/users/me/onboarding",
        json={"project_id": str(uuid.uuid4()), "project_name": "x"},
    )
    assert resp.status_code == 401
