"""任务页不用刷新就看得见它的卡：递上来、被退回、带着的那几行提示。

任务页的 socket 开在任务自己那段对话上。一张卡递进来、被退回，开着任务页的人
应该当场在输入框上方看见它变，而对话里那一行（「…递了验收卡」「…退回了」）也
应该当场长出来 —— 不是等下一次刷新。
"""

import uuid

import pytest

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import (
    chat_ws_url,
    post_project,
    session_auth_headers,
)
from tests.integration.test_accept_pr import app_world as app_world

pytestmark = pytest.mark.usefixtures("app_world")


def _task(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    room = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return room, str(delivery_task_id(client, room))


def _heard(ws, wanted, rounds: int = 20) -> list[dict]:
    """What the socket hears until ``wanted`` is among it. A ping is answered
    after everything queued before it, so each pong closes one round; a frame
    that never comes ends the test instead of hanging it."""
    frames: list[dict] = []
    for _ in range(rounds):
        ws.send_json({"type": "ping"})
        while (frame := ws.receive_json())["type"] != "pong":
            frames.append(frame)
        if wanted(frames):
            return frames
    raise AssertionError(f"never heard it; heard {frames}")


def _card_changed(frames: list[dict]) -> bool:
    """The card box and the task's progress both read again."""
    return all(
        {"type": "state", "resource": resource} in frames
        for resource in ("accept", "tasks")
    )


def _notice(event_type: str):
    def seen(frames: list[dict]) -> bool:
        return any(
            f["type"] == "event_block"
            and (f["block"].get("meta") or {}).get("event_type") == event_type
            for f in frames
        )

    return seen


def test_the_task_page_hears_its_card_filed_and_returned(client):
    room, task = _task(client)

    with client.websocket_connect(chat_ws_url(task, "alice")) as page:
        filed = client.post(
            f"/topics/{task}/accept-card",
            headers=delivery_headers(client, room),
            json={
                "change_subject": "chore(test): file an accept card",
                "reviewer_handle": "alice",
                "focus": "最懂",
            },
        )
        assert filed.status_code == 200, filed.text
        heard = _heard(page, lambda fs: _card_changed(fs) and _notice("card_filed")(fs))
        line = next(f["block"] for f in heard if f["type"] == "event_block")
        assert line["conversation_id"] == task

        returned = client.post(
            f"/accept-cards/{filed.json()['data']['id']}/reject",
            json={"decided_by": "alice", "note": "改成英文"},
            headers=session_auth_headers("alice"),
        )
        assert returned.status_code == 200, returned.text
        _heard(page, lambda fs: _card_changed(fs) and _notice("card_rejected")(fs))


def test_a_card_that_fails_to_land_tells_no_page(client):
    """提交之后才说：一次被拒的递卡什么都没写下，页面也就什么都不该听到。"""
    room, task = _task(client)

    with client.websocket_connect(chat_ws_url(task, "alice")) as page:
        refused = client.post(
            f"/topics/{task}/accept-card",
            headers=delivery_headers(client, room),
            json={
                "change_subject": "chore(test): file an accept card",
                "reviewer_handle": f"nobody-{uuid.uuid4().hex[:6]}",
                "focus": "最懂",
            },
        )
        assert refused.status_code >= 400
        page.send_json({"type": "ping"})
        heard = []
        while (frame := page.receive_json())["type"] != "pong":
            heard.append(frame)
        assert not _card_changed(heard)
