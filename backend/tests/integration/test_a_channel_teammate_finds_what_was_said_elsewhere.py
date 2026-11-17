"""A channel's AI teammate searches its whole project by keyword (`cheese_find`),
and finds only what everyone its answer reaches may read: a word said in a
private channel is not handed to a channel whose people cannot read it."""

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _project(client) -> dict:
    """dave's project; alice and bob are on its team."""
    for who in ("alice", "bob", "dave"):
        seed_user(client, who)
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob"):
        join_project_team(client, p["id"], handle)
    return p


def _channel(client, pid: str, title: str, *, private: bool) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": title, "members_only": private},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seated_agent(client, tid: str) -> str:
    rows = client.get(
        f"/topics/{tid}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(row["member_handle"] for row in rows if row["agent"])


def _find(client, pid: str, tid: str, query: str) -> str:
    """What the teammate seated in ``tid`` is told when it searches."""
    token = mint_scoped_token(
        project_id=pid,
        topic_id=tid,
        access_scope="project",
        agent_handle=_seated_agent(client, tid),
    )
    r = client.post(
        "/assistant/agent/search",
        json={"query": query},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["text"]


def _said(client, tid: str, text: str) -> str:
    return post_message(client, tid, "alice", {"content": text})["id"]


def test_a_channel_teammate_finds_what_was_said_in_another_channel(client):
    p = _project(client)
    ops = _channel(client, p["id"], "运维", private=False)
    said = _said(client, ops, "部署脚本放在 ops 目录下面")

    found = _find(client, p["id"], p["root_topic_id"], "部署脚本")

    assert said in found, "命中带着读它全文要用的消息编号"
    assert ops in found, "命中带着它所在的频道"


def test_what_a_private_channel_said_stays_out_of_a_wider_channel(client):
    p = _project(client)
    secret = _channel(client, p["id"], "机密", private=True)
    said = _said(client, secret, "预算表放在共享盘第三层")

    assert said not in _find(client, p["id"], p["root_topic_id"], "预算表"), (
        "bob 读不到机密频道，答案会落到他也在的频道里"
    )
    assert said in _find(client, p["id"], secret, "预算表"), (
        "机密频道自己的队友照样搜得到"
    )
