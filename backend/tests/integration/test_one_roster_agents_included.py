"""一张名册，队友也在上面（结论 12；ARCH §9.4「名册」那一行；不变量 I4a、I9）。

「这个项目里有谁」以前有两个答案：``ProjectMember``（只有人）和 ``topic_memberships``
（人加 agent 的席位）。读项目名册的那条路走的是前一个，于是一个 agent 在项目里列不
出另一个 agent——结论 12 那句「不同 handle 之间只走 chat，agent 对 agent 也是」在代
码上没有输入：列不出来的名字 @ 不成一个 token，也就通知不到任何人，那条 chat 等于
没发出去。

现在只有一个读法（``membership/roster.py`` 的 ``roster()``）。``ProjectMember`` 仍是
人的授权行、席位仍是席位，它们是同一张名册的两个**来源**，不是两张名册。

名册这张单子发给谁：人从 ``GET /projects/{id}/members`` 拿（那道门认人——一轮里铸出
来的凭据过不了 ``authorize_project``，见 ``projects.py`` 的 ``_artifact_keeper``），
队友的那一份由平台在组这一轮时直接递给它。所以下面第一条断言这张单子上有谁，第二条
断言队友手上那份确实到了：它照着名字喊出来的那个队友，真的被点到、被通知到。
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


def _retire(client, project_id: str, instance_id: str) -> None:
    gone = client.delete(f"/projects/{project_id}/agents/{instance_id}")
    assert gone.status_code == 200, gone.text


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


def _roster(client, project_id: str) -> dict[str, dict]:
    rows = client.get(
        f"/projects/{project_id}/members", headers=session_auth_headers(OWNER)
    )
    assert rows.status_code == 200, rows.text
    return {row["user_handle"]: row for row in rows.json()["data"]["data"]}


def test_the_project_roster_lists_people_and_teammates_together(client):
    """验收①：一张名册上，人和这个项目的队友都在，每一位带着自己的名字。

    队友那一行带的是它**坐名册用的** handle（@ 得到、通知得到的那一个），不是它记
    忆池的 key；停用的队友还在名册上（它在已经接手的房间里照常工作），只是标着停用，
    派新活的地方据此把它滤掉。
    """
    project = _project(client)
    project_id = project["id"]
    reviewer = _teammate(client, project_id, "reviewer", "评审")
    retired = _teammate(client, project_id, "old-hand", "退休")
    _retire(client, project_id, retired["id"])

    roster = _roster(client, project_id)

    assert OWNER in roster, sorted(roster)
    assert roster[OWNER]["agent"] is False
    assert roster[OWNER]["active"] is True

    seen = roster.get(reviewer["seat_handle"])
    assert seen is not None, f"评审不在名册上：{sorted(roster)}"
    assert seen["agent"] is True
    assert seen["name"] == "评审"
    assert seen["active"] is True

    gone = roster.get(retired["seat_handle"])
    assert gone is not None, f"退休的队友掉出了名册：{sorted(roster)}"
    assert gone["active"] is False


def test_an_agent_can_chat_the_teammate_it_could_not_see(client):
    """验收②：队友手上那张名册上有另一位队友，所以 A 对 B 发得出一条 chat。

    两件事一起断言：存下来的正文里是结构化的 ``<@handle>``（不是一段谁也点不动的
    「@评审」原文），以及 B 收到一条强提醒。名册解析不到这个名字，两件事一件都不会
    发生——这正是结论 12 以前在代码上跑不起来的样子。

    评审没坐在这间房里：@ 一位还没进这间房的队友，和 @ 一个还没进来的人是同一件事。
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
