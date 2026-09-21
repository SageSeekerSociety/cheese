"""一个参与者不按「它是不是 agent」分类，按它**坐在哪**分类。

两条缝，两条判据：

**缝一：这条凭证能不能替这个房间发言。** 席位即授权。一个属于这个项目、因而进得来
这些房间的队友，在它**没有席位**的那个房间里不能以这个房间的名义说话；坐下之后同一条
凭证就可以。原来这里问的是调用者**是不是** agent（一个在信任边界解析、八条路由之外才
被读到的布尔），而那个答案在整个项目里都一样，于是没坐下的那位照样发得出来。

这条判据不放宽成「这个项目认不认它」，哪怕只放宽一点：总览的花名册照着整个项目，
所以「根房间认不认它」等于「项目认不认它」，撤掉的席位就白撤了。也没有任何一个
handle 例外——项目凭证认证的是这个项目自己那位芝士，而它在自己进得去的每一间房里
都真的有席位，所以照房间花名册答就够了。

**缝二（I9b）：一个 agent 实例只能在建它的那个项目里持有席位。** 人没有这条限制
——被邀请到哪就去哪。这是「谁拥有这个参与者」的直接后果：实例由建它的项目拥有，
它的记忆池也关在那个项目里，别的项目里没有任何东西寻址得到它。
"""

import uuid

from app.core.sandbox_auth import mint_project_agent_credential, mint_scoped_token
from app.domain.identity.handles import agent_instance_handle
from tests.integration.conftest import session_auth_headers


def _project(client, name, owner="alice"):
    return client.post("/projects", json={"name": name, "owner_handle": owner}).json()[
        "data"
    ]


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
    room = _room(client, project)

    made = client.post(
        f"/projects/{project['id']}/agents",
        json={"handle": "planner", "display_name": "规划师"},
    )
    assert made.status_code == 200, made.text
    seat = agent_instance_handle(made.json()["data"]["id"])

    # On the project's roster, so it is allowed to act in this project's rooms.
    # This room is not where it sits.
    joined = client.post(
        f"/projects/{project['id']}/members",
        json={"user_handle": seat, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text

    headers = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project["id"], topic_id=room["id"], agent_handle=seat
        )
    }
    refused = _publish(client, room["id"], headers)
    assert refused.status_code == 403, refused.text

    seated = client.post(
        f"/topics/{room['id']}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert seated.status_code == 200, seated.text

    # Same credential, same room, same participant. What changed is the seat.
    allowed = _publish(client, room["id"], headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["author"] == seat


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


def test_a_seat_in_the_root_room_is_not_a_seat_in_every_room(client):
    """根房间的席位只管根房间，不是全项目通行证。

    总览的花名册照着整个项目（`seed_root` 把每一位成员都播进去），所以拿「根房间
    的花名册认不认它」当兜底，等于把判据从「这个房间认不认它」放回「这个项目认不
    认它」——一个被从房间 X 撤掉席位的队友照样发得出来，而撤席位就是撤授权正是这
    整件事存在的理由。

    没有兜底：项目凭证认证的那位芝士自己就坐在房间里，所以它和别的队友一样，照
    房间的花名册答。
    """
    project = _project(client, "Root seat is not a project pass")
    root = client.get(f"/projects/{project['id']}").json()["data"]["root_topic_id"]
    room = _room(client, project, title="它没坐进来的那个房间")

    made = client.post(
        f"/projects/{project['id']}/agents",
        json={"handle": "planner", "display_name": "规划师"},
    )
    assert made.status_code == 200, made.text
    seat = agent_instance_handle(made.json()["data"]["id"])

    joined = client.post(
        f"/projects/{project['id']}/members",
        json={"user_handle": seat, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    seated = client.post(
        f"/topics/{root}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert seated.status_code == 200, seated.text

    def publish(topic_id):
        return _publish(
            client,
            topic_id,
            {
                "X-Cheese-Token": mint_scoped_token(
                    project_id=project["id"], topic_id=topic_id, agent_handle=seat
                )
            },
        )

    # 它真的坐在根房间：同一条凭证在那里发得出来。
    at_root = publish(root)
    assert at_root.status_code == 200, at_root.text

    # 而房间 X 没给它席位，所以在那里它不是这个房间的 agent。
    refused = publish(room["id"])
    assert refused.status_code == 403, refused.text


def _project_credential(client, project) -> tuple[str, str]:
    """一张项目凭证，和它代表的那位芝士的 handle。

    项目凭证认证的是**这个项目自己**的那位芝士，到哪个房间都不换身份。签发本身不
    给角色，所以这里照 `test_project_agent_credential` 的做法显式把它加进项目成员
    ——凭证的可达范围来自它已有的成员身份。
    """
    rows = client.get(f"/projects/{project['id']}/agents").json()["data"]["data"]
    (default,) = [row for row in rows if row["is_default"]]
    handle = default["seat_handle"]
    members = client.get(f"/projects/{project['id']}/members").json()["data"]["data"]
    if not any(m["user_handle"] == handle for m in members):
        joined = client.post(
            f"/projects/{project['id']}/members",
            json={"user_handle": handle},
            headers=session_auth_headers("alice"),
        )
        assert joined.status_code == 200, joined.text
    return mint_project_agent_credential(project_id=project["id"], epoch=0), handle


def test_the_project_s_own_credential_speaks_in_every_room_of_it(client):
    """席位问的是「这个房间认不认它」，而项目凭证的那位芝士在每一间房里都有席位。

    一张线下的项目凭证（本地 agent、bot、CI 拿的都是它）在任意一个房间里，handle
    都是同一位芝士自己的那一个，而建房时就把它的席位播了进去——所以它发得出来，
    靠的是花名册上真有那一行，不是靠一条豁免。撤掉那一行，同一张凭证在这间房里就
    该被拒（下一条用例）。
    """
    project = _project(client, "Project credential speaks")
    room = _room(client, project, title="不是根房间")
    token, handle = _project_credential(client, project)

    published = _publish(client, room["id"], {"X-Cheese-Token": token})
    assert published.status_code == 200, published.text
    assert published.json()["data"]["author"] == handle


def test_the_question_that_credential_asks_is_not_an_input_it_must_read(client):
    """同一条判据的另一半：它自己问出口的题，不该再回头当成一条没读过的话。

    `/ask` 盖不盖待读标记就看这一问。答错了，「忘了 @」的补救按钮不再答「没有待读
    的东西」，白开一轮，而那一轮的 prompt 里躺着它刚问出口的这道题。
    """
    project = _project(client, "Project credential asks")
    room = _room(client, project, title="不是根房间")
    token, _ = _project_credential(client, project)

    asked = client.post(
        f"/topics/{room['id']}/ask",
        json={"question": "先做哪一个？", "options": ["A", "B"]},
        headers={"X-Cheese-Token": token},
    )
    assert asked.status_code == 200, asked.text

    summoned = client.post(
        f"/topics/{room['id']}/summon",
        json={},
        headers=session_auth_headers("alice"),
    )
    assert summoned.status_code == 200, summoned.text
    assert summoned.json()["data"] == {
        "started": False,
        "reason": "nothing_pending",
    }, "它自己问出口的那道题不该把它自己叫起来"


def test_revoking_that_seat_closes_the_project_credential_too(client):
    """撤掉席位就是撤掉授权——对线下那张项目凭证也一样。

    以前不是：凭证认证的是一个从根房间派生的名字，它在任何别的房间都没有席位，所以
    判据里给它开了一个豁免。名字换成这位芝士自己的之后，那个豁免就成了一个洞：这间
    房把它的席位撤了，凭证照样写得进来。所以豁免没了，这条用例守着它别回来。
    """
    project = _project(client, "Revoking the seat revokes the credential")
    room = _room(client, project, title="把它请出去的那个房间")
    token, handle = _project_credential(client, project)

    assert _publish(client, room["id"], {"X-Cheese-Token": token}).status_code == 200

    removed = client.delete(
        f"/topics/{room['id']}/members/{handle}",
        headers=session_auth_headers("alice"),
    )
    assert removed.status_code == 200, removed.text

    refused = _publish(client, room["id"], {"X-Cheese-Token": token})
    assert refused.status_code == 403, refused.text
