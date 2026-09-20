"""一个参与者不按「它是不是 agent」分类，按它**坐在哪**分类。

两条缝，两条判据：

**缝一：这条凭证能不能替这个房间发言。** 席位即授权 —— 撤销是删一行，不是等
token 过期。凭证本身没变、也没过期，但它名下的那位已经不在这个房间的名册上了，
所以它在这里不再是「这个房间的芝士」。原来这里问的是调用者**是不是** agent
（一个在信任边界解析、八条路由之外才被读到的布尔），于是撤了席位照样发得出来。

**缝二（I9b）：一个 agent 实例只能在建它的那个项目里持有席位。** 人没有这条限制
——被邀请到哪就去哪。这是「谁拥有这个参与者」的直接后果：实例由建它的项目拥有，
它的记忆池也关在那个项目里，别的项目里没有任何东西寻址得到它。
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.identity.handles import agent_instance_handle
from tests.integration.conftest import session_auth_headers


def _project(client, name, owner="alice"):
    return client.post(
        "/projects", json={"name": name, "owner_handle": owner}
    ).json()["data"]


def _room(client, project, title="Room", created_by="alice"):
    return client.post(
        "/topics",
        json={"project_id": project["id"], "title": title, "created_by": created_by},
    ).json()["data"]


def _publish(client, topic_id, headers):
    return client.post(
        f"/topics/{topic_id}/messages",
        json={"content": "我先核对当前流程。", "request_id": str(uuid.uuid4())},
        headers=headers,
    )


def test_publishing_needs_a_seat_in_this_room_not_an_agent_shaped_caller(client):
    project = _project(client, "Seat is the grant")
    topic = _room(client, project)
    headers = {"X-Cheese-Token": mint_scoped_token(
        project_id=project["id"], topic_id=topic["id"]
    )}

    assert _publish(client, topic["id"], headers).status_code == 200

    roster = client.get(
        f"/topics/{topic['id']}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    seats = [row["handle"] for row in roster if row["agent"]]
    assert seats, roster
    for seat in seats:
        removed = client.delete(
            f"/topics/{topic['id']}/members/{seat}",
            headers=session_auth_headers("alice"),
        )
        assert removed.status_code == 200, removed.text

    # Same token, same TTL, same caller. What changed is the roster.
    refused = _publish(client, topic["id"], headers)
    assert refused.status_code == 403, refused.text


def test_a_teammate_seats_only_in_the_project_that_built_it(client):
    home = _project(client, "Home")
    elsewhere = _project(client, "Elsewhere")
    theirs = _room(client, elsewhere)

    agent = client.post(
        f"/projects/{home['id']}/agents",
        json={"handle": "planner", "display_name": "规划师"},
    )
    assert agent.status_code == 200, agent.text
    seat = agent_instance_handle(agent.json()["data"]["id"])

    refused = client.post(
        f"/topics/{theirs['id']}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert refused.status_code == 404, refused.text

    # 它在自家项目的房间里坐得下 —— 被拒的是跨项目，不是「这是个 agent」。
    ours = _room(client, home, title="Home room")
    seated = client.post(
        f"/topics/{ours['id']}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert seated.status_code == 200, seated.text

    # 人没有这条限制：同一个 handle 在两个项目的房间里都坐得下。
    for room in (theirs, ours):
        joined = client.post(
            f"/topics/{room['id']}/members",
            json={"handle": "bob", "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert joined.status_code == 200, joined.text
