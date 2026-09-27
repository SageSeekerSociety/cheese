"""改验收人 — reassign a pending accept card (spec §4.4)."""

from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.integration.test_accept import _make_card
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_pr import _rendered_head
from tests.integration.test_accept_pr import app_world as app_world


def _topic_and_card(client) -> str:
    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    # 2026-09-26: /reassign 和被指派的审阅人一起，现在都要求话题成员资格
    # (`_card_actor`)。user-1 是被指派的那个人，alice 是改派的人 —— 两个都得真在
    # 这个项目里，才轮得到改派本身那条规则被检验。改派的目标（mentor-1）不需要是
    # 成员：显式指定优先没变，只是指定一个房间外的人会得到一张他采纳不了的卡。
    for handle in ("user-1", "alice"):
        join_project_team(client, p["id"], handle)
    return _make_card(client, t["id"], "user-1")


def test_reassign_changes_reviewer(client):
    card_id = _topic_and_card(client)
    r = client.post(
        f"/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "mentor-1", "routing_reason": "导师更合适"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["reviewer_handle"] == "mentor-1"
    assert r.json()["data"]["routing_reason"] == "导师更合适"


def test_cannot_reassign_decided_card(client):
    card_id = _topic_and_card(client)
    accepted = client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "user-1", "head_sha": _rendered_head(client, card_id)},
        headers=session_auth_headers("user-1"),
    )
    assert accepted.status_code == 200, accepted.text
    r = client.post(
        f"/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "user-2"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
