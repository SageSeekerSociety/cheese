"""A person in a room sets「到点提醒我」through the same route an AI teammate uses.

Who may set one is a question about the room (is this caller let in), never about
whether the caller is an agent. The reminder goes to whoever set it, to them
alone: it reaches their inbox and does not appear in the room everyone reads.

Requests here go out the way a browser sends them, with a session and no
`X-Cheese-Token`; the test client otherwise attaches the sandbox token to
every request.
"""

from datetime import UTC, datetime, timedelta

from app.domain.delivery.timer import deliver_due
from tests.conftest import seed_user
from tests.integration.conftest import post_project

BROWSER = {"X-Cheese-Token": ""}


def _room(client, owner: str) -> str:
    project = post_project(client, json={"name": "提醒", "owner_handle": owner})
    assert project.status_code == 200, project.text
    room = client.post(
        "/topics",
        json={
            "project_id": project.json()["data"]["id"],
            "title": "周会",
            "created_by": owner,
        },
    )
    assert room.status_code == 200, room.text
    return room.json()["data"]["id"]


def _as(token: str) -> dict[str, str]:
    return {**BROWSER, "Authorization": f"Bearer {token}"}


def _remind(client, room: str, headers: dict, *, at: datetime, content: str):
    return client.post(
        f"/topics/{room}/deliveries",
        headers=headers,
        json={"at": at.isoformat(), "content": content},
    )


def _inbox(client, token: str) -> list[dict]:
    r = client.get("/notifications", headers=_as(token))
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


class _NoAgentWork:
    def submit(self, *args, **kwargs):
        raise AssertionError("a person's reminder started an agent")


def _fire(client) -> None:
    client.portal.call(
        lambda: deliver_due(
            client.test_request_factory, chat=object(), runner=_NoAgentWork()
        )
    )


def test_a_member_is_reminded_in_their_own_inbox_and_nowhere_else(client):
    token = seed_user(client, "mei")
    other = seed_user(client, "otto")
    room = _room(client, "mei")
    timeline_before = client.get(f"/topics/{room}/blocks").json()["data"]["data"]

    r = _remind(
        client,
        room,
        _as(token),
        at=datetime.now(UTC) - timedelta(seconds=1),
        content="交周报",
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["to"] == "mei"
    assert not [n for n in _inbox(client, token) if "交周报" in str(n)], (
        "the reminder arrived before its time"
    )

    _fire(client)

    mine = [n for n in _inbox(client, token) if "交周报" in str(n)]
    assert len(mine) == 1, _inbox(client, token)
    assert room in str(mine[0]), "the reminder does not lead back to its room"
    assert not [n for n in _inbox(client, other) if "交周报" in str(n)]
    timeline = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    assert len(timeline) == len(timeline_before), (
        "a personal reminder was posted in the room"
    )


def test_a_reminder_set_for_later_waits_for_its_time(client):
    token = seed_user(client, "mei")
    room = _room(client, "mei")

    r = _remind(
        client,
        room,
        _as(token),
        at=datetime.now(UTC) + timedelta(hours=1),
        content="交周报",
    )
    assert r.status_code == 200, r.text
    _fire(client)

    assert not [n for n in _inbox(client, token) if "交周报" in str(n)]


def test_someone_outside_the_room_cannot_set_one_there(client):
    seed_user(client, "mei")
    stranger = seed_user(client, "sam")
    room = _room(client, "mei")

    r = _remind(
        client,
        room,
        _as(stranger),
        at=datetime.now(UTC) - timedelta(seconds=1),
        content="打扰一下",
    )
    assert r.status_code == 403, r.text
    _fire(client)
    assert not [n for n in _inbox(client, stranger) if "打扰一下" in str(n)]


def test_a_caller_with_no_credential_is_refused(client):
    seed_user(client, "mei")
    room = _room(client, "mei")

    r = _remind(
        client,
        room,
        BROWSER,
        at=datetime.now(UTC) - timedelta(seconds=1),
        content="谁也不是",
    )
    assert r.status_code == 401, r.text


def test_the_recipient_is_the_caller_whatever_the_body_says(client):
    token = seed_user(client, "mei")
    other = seed_user(client, "otto")
    room = _room(client, "mei")

    r = client.post(
        f"/topics/{room}/deliveries",
        headers=_as(token),
        json={
            "at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            "content": "给 otto 的",
            "to": "otto",
            "recipient": "otto",
        },
    )
    assert r.status_code == 200, r.text
    _fire(client)

    assert [n for n in _inbox(client, token) if "给 otto 的" in str(n)]
    assert not [n for n in _inbox(client, other) if "给 otto 的" in str(n)]
