"""一张名册，队友也在上面（结论 12；ARCH §9.4「名册」那一行；不变量 I4a、I9）。

「这个项目里有谁」以前有两个答案：``ProjectMember``（只有人）和 ``topic_memberships``
（人加 agent 的席位）。读名册的那条路走的是前一个，于是一个 agent 在项目里列不出
另一个 agent——结论 12 那句「不同 handle 之间只走 chat，agent 对 agent 也是」在代码
上没有输入：列不出来的东西，@ 不到，也就发不出那条 chat。

现在只有一个读法（``membership/roster.py`` 的 ``roster()``）。``ProjectMember`` 仍是
人的授权行、席位仍是席位，它们是同一张名册的两个**来源**，不是两张名册。
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import session_auth_headers

OWNER = "alice"


def _project(client) -> dict:
    made = client.post("/projects", json={"name": "一张名册", "owner_handle": OWNER})
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _teammate(client, project_id: str, handle: str, name: str) -> dict:
    made = client.post(
        f"/projects/{project_id}/agents",
        json={"handle": handle, "display_name": name},
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _room(client, project_id: str) -> str:
    made = client.post(
        "/topics",
        json={"project_id": project_id, "title": "房间", "created_by": OWNER},
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


def _seat(client, room: str, handle: str) -> None:
    joined = client.post(
        f"/topics/{room}/members",
        json={"handle": handle, "role": "member"},
        headers=session_auth_headers(OWNER),
    )
    assert joined.status_code == 200, joined.text


def _as_teammate(project_id: str, room: str, seat: str) -> dict:
    """这一轮的凭据，署名是坐在 ``room`` 里的那位队友。"""
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project_id,
            topic_id=room,
            access_scope="project",
            agent_handle=seat,
        )
    }


def _roster(client, project_id: str, headers: dict) -> dict[str, dict]:
    rows = client.get(f"/projects/{project_id}/members", headers=headers)
    assert rows.status_code == 200, rows.text
    return {row["user_handle"]: row for row in rows.json()["data"]["data"]}


def test_an_agent_lists_the_other_agents_in_its_project(client):
    """验收①：项目里的 agent A 列得出同项目的 agent B。

    列出来的那一行带的是 B 自己的名字和它坐名册用的 handle——@ 得到、通知得到的
    那一个，不是它记忆池的 key。
    """
    project = _project(client)
    project_id = project["id"]
    planner = _teammate(client, project_id, "planner", "规划师")
    reviewer = _teammate(client, project_id, "reviewer", "评审")
    room = _room(client, project_id)
    _seat(client, room, planner["seat_handle"])

    roster = _roster(
        client, project_id, _as_teammate(project_id, room, planner["seat_handle"])
    )

    seen = roster.get(reviewer["seat_handle"])
    assert seen is not None, f"评审不在名册上：{sorted(roster)}"
    assert seen["agent"] is True
    assert seen["name"] == "评审"
    # 人没有因此从名册上掉下去：合的是读法，不是把一份换成另一份。
    assert OWNER in roster, sorted(roster)
    assert roster[OWNER]["agent"] is False


def test_an_agent_can_chat_the_teammate_it_just_listed(client):
    """验收②：列得出，就 @ 得到——A 对 B 发的那条 chat 真的送到了 B 手上。

    两件事一起断言：存下来的正文里是结构化的 ``<@handle>``（不是一段谁也点不动的
    「@评审」原文），以及 B 收到一条强提醒。名册解析不到的名字两件事都不会发生。
    """
    project = _project(client)
    project_id = project["id"]
    planner = _teammate(client, project_id, "planner", "规划师")
    reviewer = _teammate(client, project_id, "reviewer", "评审")
    room = _room(client, project_id)
    _seat(client, room, planner["seat_handle"])

    sent = client.post(
        f"/topics/{room}/messages",
        json={
            "content": "@评审 这一版你看一下",
            "request_id": "7c9f3d1e-9b42-4a5e-8f61-2d0c4b6a1e53",
        },
        headers=_as_teammate(project_id, room, planner["seat_handle"]),
    )
    assert sent.status_code == 200, sent.text
    assert f"<@{reviewer['seat_handle']}>" in sent.json()["data"]["content"]

    alerts = client.get(
        f"/projects/{project_id}/alerts",
        headers=session_auth_headers(reviewer["seat_handle"]),
    )
    assert alerts.status_code == 200, alerts.text
    titles = [row["title"] for row in alerts.json()["data"]["data"]]
    assert len(titles) == 1, titles
    assert "房间" in titles[0]
