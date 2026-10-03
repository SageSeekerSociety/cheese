"""A push is written in its recipient's language.

Web Push hands the browser finished text — it is encrypted for that browser and
shown as it arrives — so the server has to know which language each person
reads and say the sentence in it. Stated from the recipient's side: the
language they picked stays picked wherever they sign in, and the push that
reaches their browser is in it; a line the catalog cannot say in it arrives as
the room stored it.
"""

import asyncio
import json
import uuid

import pytest

from app.common.auth import create_access_token
from app.core.sentences import notice_message, say, with_keys
from app.domain.notification import push_delivery
from app.domain.notification.handlers import NotificationDelivery
from app.domain.notification.models import NotificationType
from app.domain.notification.outbox import ChannelIntentHandler, drain_channel
from app.domain.user.repositories import UserRepository


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pick(client, token: str, language: str):
    return client.put(
        "/users/me/language", json={"language": language}, headers=_headers(token)
    )


def _sign_in(api_client, person) -> dict:
    """A fresh sign-in, as the page makes it: its own session and token."""
    resp = api_client.post(
        "/users/auth/login",
        json={"username": person.username, "password": person.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _person(client, handle: str, language: str | None = None) -> tuple[int, str]:
    """Someone who has picked ``language`` on the page, and their token."""
    holder: dict[str, int] = {}

    async def seed() -> None:
        async with client.test_factory() as session:
            user = await UserRepository(session).create_user(
                username=handle, email=f"{handle}@example.com"
            )
            holder["id"] = user.id
            await session.commit()

    asyncio.run(seed())
    token = create_access_token(holder["id"], handle=handle)
    if language is not None:
        assert _pick(client, token, language).status_code == 200
    return holder["id"], token


def _subscribe(client, token: str, endpoint: str) -> None:
    resp = client.put(
        "/push/subscriptions",
        json={"endpoint": endpoint, "p256dh": "key", "auth": "auth"},
        headers=_headers(token),
    )
    assert resp.status_code == 200, resp.text


def _room_notice() -> dict:
    """A ROOM_NOTICE payload as the room writes it: the Chinese line and its key."""
    line = say("acceptReady", pr=12, reviewer="ana")
    meta = with_keys(None, content=line)
    return {"content": str(line), "topicTitle": "预算复核", **notice_message(meta)}


def _pushed(client, monkeypatch, deliveries: list[NotificationDelivery]) -> dict:
    """What each subscribed browser is handed, by endpoint."""
    sent: dict[str, dict] = {}

    def send_one(*, endpoint, p256dh, auth, body):
        sent[endpoint] = json.loads(body)
        return 201

    monkeypatch.setattr(push_delivery, "_send_one", send_one)

    async def go() -> None:
        factory = client.test_request_factory
        async with factory() as session:
            await ChannelIntentHandler(session, push_enabled=True).send_batch(
                deliveries
            )
            await session.commit()
        await drain_channel(
            factory, channel="push", batch_size=len(deliveries), max_attempts=1
        )

    client.portal.call(go)
    return sent


def _delivery(user_id: int, payload: dict, type_=NotificationType.ROOM_NOTICE):
    return NotificationDelivery(
        recipient_id=user_id,
        type=type_,
        payload=payload,
        delivery_key=f"test:{uuid.uuid4()}",
    )


def test_the_language_picked_is_kept_for_the_next_sign_in(api_client, user_client):
    person = user_client.create_user()
    first = _sign_in(api_client, person)
    assert first["user"]["language"] is None
    assert _pick(api_client, first["accessToken"], "en").status_code == 200

    again = _sign_in(api_client, person)
    assert again["user"]["language"] == "en"

    assert _pick(api_client, again["accessToken"], "zh-CN").status_code == 200
    me = api_client.get("/users/me", headers=_headers(first["accessToken"]))
    assert me.json()["data"]["user"]["language"] == "zh-CN"


@pytest.mark.parametrize("language", ["fr", "", "EN"])
def test_a_language_nobody_can_pick_is_refused(api_client, user_client, language):
    person = user_client.create_user()
    token = _sign_in(api_client, person)["accessToken"]
    assert _pick(api_client, token, language).status_code == 400
    assert _sign_in(api_client, person)["user"]["language"] is None


def test_someone_else_never_sees_the_language(api_client, user_client):
    alice, bob = user_client.create_user(), user_client.create_user()
    _pick(api_client, _sign_in(api_client, alice)["accessToken"], "en")
    resp = api_client.get(
        f"/users/{alice.user_id}",
        headers=_headers(_sign_in(api_client, bob)["accessToken"]),
    )
    assert resp.status_code == 200, resp.text
    assert "language" not in resp.json()["data"]["user"]


def test_each_browser_gets_the_push_in_its_owners_language(client, monkeypatch):
    people = {
        "https://push.example/en": _person(client, "eve", "en"),
        "https://push.example/zh": _person(client, "zhou", "zh-CN"),
        "https://push.example/none": _person(client, "nina"),
    }
    for endpoint, (_, token) in people.items():
        _subscribe(client, token, endpoint)

    sent = _pushed(
        client,
        monkeypatch,
        [_delivery(user_id, _room_notice()) for user_id, _ in people.values()],
    )

    shown = {endpoint: (p["title"], p["body"]) for endpoint, p in sent.items()}
    chinese = ("PR #12 可以合并了，等 ana 采纳", "在「预算复核」")
    assert shown == {
        "https://push.example/en": (
            "PR #12 is ready to merge, waiting for ana to accept",
            "In “预算复核”",
        ),
        "https://push.example/zh": chinese,
        "https://push.example/none": chinese,
    }


def test_a_line_without_a_key_arrives_as_the_room_stored_it(client, monkeypatch):
    user_id, token = _person(client, "ivan", "en")
    _subscribe(client, token, "https://push.example/ivan")

    sent = _pushed(
        client,
        monkeypatch,
        [_delivery(user_id, {"content": "装之前的事", "topicTitle": "迁移"})],
    )

    # One language per notification: the room only has this line in Chinese.
    pushed = sent["https://push.example/ivan"]
    assert (pushed["title"], pushed["body"]) == ("装之前的事", "在「迁移」")


def test_a_question_keeps_its_own_words_inside_the_recipients(client, monkeypatch):
    user_id, token = _person(client, "kira", "en")
    _subscribe(client, token, "https://push.example/kira")

    sent = _pushed(
        client,
        monkeypatch,
        [
            _delivery(
                user_id,
                {"question": "用哪个数据库？", "topicTitle": "迁移"},
                NotificationType.CHEESE_QUESTION,
            )
        ],
    )

    pushed = sent["https://push.example/kira"]
    assert (pushed["title"], pushed["body"]) == ("用哪个数据库？", "In “迁移”")
