"""The desktop app's live notices connection (`app/api/routes/notifications_live.py`).

What the app relies on, stated from its side: it hears about a notice browser
push would carry the moment it is committed, with the push's own words; it
catches up after being away without seeing anything twice or anything from
before it first connected; signing out ends it; and the credential it keeps
opens nothing else.
"""

import asyncio
import uuid

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.common.auth import create_access_token
from app.core.sentences import notice_message, say, with_keys
from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
)
from app.domain.notification.models import NotificationType
from app.domain.user.repositories import UserRepository
from app.domain.user.sessions import SessionService


def _signed_in(client: TestClient, handle: str) -> tuple[int, uuid.UUID, str]:
    """A person with a real sign-in session, and the page's access token for it."""
    holder: dict = {}

    async def seed() -> None:
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            users = UserRepository(db)
            user = await users.get_by_username(handle) or await users.create_user(
                username=handle, email=f"{handle}@example.com"
            )
            started = await SessionService(db).start(
                user.id, "password", ip="127.0.0.1", user_agent="test"
            )
            holder.update(user_id=user.id, sid=started.session_id)
            await db.commit()

    asyncio.run(seed())
    token = create_access_token(holder["user_id"], handle, sid=holder["sid"])
    return holder["user_id"], holder["sid"], token


def _notices_token(client: TestClient, page_token: str) -> str:
    resp = client.post(
        "/notifications/live-token", headers={"Authorization": f"Bearer {page_token}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _deliver(client: TestClient, user_id: int, *notices: tuple[NotificationType, dict]):
    """What the delivery ledger does: write the inbox rows, then commit once. It
    runs on the app's own loop, where the open connection is waiting."""

    async def deliver() -> None:
        async with client.test_request_factory() as db:  # type: ignore[attr-defined]
            await InAppNotificationHandler(db).send_batch(
                [
                    NotificationDelivery(
                        recipient_id=user_id,
                        type=type_,
                        payload=payload,
                        delivery_key=f"test:{uuid.uuid4()}",
                    )
                    for type_, payload in notices
                ]
            )
            await db.commit()

    assert client.portal is not None
    client.portal.call(deliver)


def _next(ws, kind: str) -> dict:
    """The next frame of this kind; beats and counts in between are skipped."""
    while True:
        frame = ws.receive_json()
        if frame["kind"] == kind:
            return frame


def _settled(ws) -> None:
    """Read up to the count the server sends after every notices frame. Past it
    the server only waits, so leaving the connection now cancels no query: the
    test client cancels the server's task the moment the block exits, and one
    cancelled while it takes a connection leaves that connection open."""
    _next(ws, "waiting")


QUESTION = {
    "question": "用哪个数据库？",
    "topicTitle": "迁移",
    "projectId": "p1",
    "topicId": "t1",
}


def test_a_notice_reaches_the_open_connection_with_the_pushs_words(client):
    user_id, _, page = _signed_in(client, "ada")
    notices = _notices_token(client, page)
    _deliver(client, user_id, (NotificationType.ROOM_NOTICE, {"content": "装之前的事"}))

    with client.websocket_connect(
        "/notifications/live", headers={"Authorization": f"Bearer {notices}"}
    ) as ws:
        ws.send_json({"after": None})
        first = _next(ws, "notices")
        # A first connection is shown nothing from before it.
        assert first["items"] == []
        _settled(ws)

        _deliver(
            client,
            user_id,
            (NotificationType.MENTION, {}),  # never pushed
            (NotificationType.CHEESE_QUESTION, QUESTION),
        )
        live = _next(ws, "notices")
        _settled(ws)

    assert [(i["title"], i["body"], i["url"]) for i in live["items"]] == [
        ("用哪个数据库？", "在「迁移」", "/projects/p1/topics/t1")
    ]


def test_the_app_speaks_the_language_the_person_picked(client):
    user_id, _, page = _signed_in(client, "eli")
    resp = client.put(
        "/users/me/language",
        json={"language": "en"},
        headers={"Authorization": f"Bearer {page}"},
    )
    assert resp.status_code == 200, resp.text
    notices = _notices_token(client, page)
    headers = {"Authorization": f"Bearer {notices}"}

    with client.websocket_connect("/notifications/live", headers=headers) as ws:
        ws.send_json({"after": None})
        stopped_at = _next(ws, "notices")["latest"] or 0
        _settled(ws)

    line = say("acceptReady", pr=7, reviewer="ana")
    room_notice = {
        "content": str(line),
        "topicTitle": "迁移",
        **notice_message(with_keys(None, content=line)),
    }
    _deliver(
        client,
        user_id,
        (NotificationType.ROOM_NOTICE, room_notice),
        (NotificationType.CHEESE_QUESTION, QUESTION),
    )

    with client.websocket_connect("/notifications/live", headers=headers) as ws:
        ws.send_json({"after": stopped_at})
        missed = _next(ws, "notices")
        _settled(ws)

    assert [(i["title"], i["body"]) for i in missed["items"]] == [
        ("PR #7 is ready to merge, waiting for ana to accept", "In “迁移”"),
        ("用哪个数据库？", "In “迁移”"),
    ]
    assert missed["away"] == "2 new notifications while you were away"


def test_coming_back_hands_over_what_came_meanwhile_once(client):
    user_id, _, page = _signed_in(client, "bea")
    notices = _notices_token(client, page)
    headers = {"Authorization": f"Bearer {notices}"}

    with client.websocket_connect("/notifications/live", headers=headers) as ws:
        ws.send_json({"after": None})
        stopped_at = _next(ws, "notices")["latest"] or 0
        _settled(ws)

    _deliver(client, user_id, (NotificationType.ROOM_NOTICE, {"content": "改动已就绪"}))

    with client.websocket_connect("/notifications/live", headers=headers) as ws:
        ws.send_json({"after": stopped_at})
        missed = _next(ws, "notices")
        _settled(ws)
    assert [i["title"] for i in missed["items"]] == ["改动已就绪"]

    with client.websocket_connect("/notifications/live", headers=headers) as ws:
        ws.send_json({"after": missed["latest"]})
        assert _next(ws, "notices")["items"] == []
        _settled(ws)


def test_signing_out_ends_the_connection(client):
    user_id, sid, page = _signed_in(client, "cai")
    notices = _notices_token(client, page)

    async def sign_out() -> None:
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            await SessionService(db).revoke(user_id, sid)
            await db.commit()

    asyncio.run(sign_out())
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/notifications/live", headers={"Authorization": f"Bearer {notices}"}
        ) as ws,
    ):
        ws.receive_json()


def test_the_apps_credential_opens_nothing_else(client):
    _, _, page = _signed_in(client, "dai")
    notices = _notices_token(client, page)
    resp = client.get(
        "/notifications/unread-count", headers={"Authorization": f"Bearer {notices}"}
    )
    assert resp.status_code == 401
