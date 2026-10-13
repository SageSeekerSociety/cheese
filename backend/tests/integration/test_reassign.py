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
    # 这个项目里，才轮得到改派本身那条规则被检验。
    # 2026-09-27: 改派的目标也不再能是房间外的人 —— 写卡这道门提前问一次
    # (`_require_reviewer_in_room`)，一个人采纳不了卡，就不该先把卡挂到他名下。
    # 所以 mentor-1 也进项目；「改派给陌生人」那条规则归
    # test_accept_reviewer_membership.py。
    for handle in ("user-1", "alice", "mentor-1"):
        join_project_team(client, p["id"], handle)
    return _make_card(client, t["id"], "user-1")


def test_reassign_changes_reviewer(client):
    card_id = _topic_and_card(client)
    r = client.post(
        f"/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "mentor-1", "focus": "导师更合适"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["reviewer_handle"] == "mentor-1"


def test_reassigning_keeps_what_the_deliverer_asked_to_check(client):
    """审阅重点是交付的人写给审阅的人的，换一个人来看，要看的还是那几件事。"""
    card_id = _topic_and_card(client)
    r = client.post(
        f"/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "mentor-1", "focus": "导师更合适"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["focus"] == "最懂"


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
