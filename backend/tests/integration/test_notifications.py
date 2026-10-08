"""项目收件箱走 HTTP —— spec §8.5/8.6, evals G2/G3.

一条通知只对一个人：广播在**写入的时候**就展开成名册上一人一行，所以这里每一条
「谁看得见什么」的判据都落在各自的收件箱上，没有一行是两个人共用的。
"""

import asyncio
import uuid

from app.domain.project.models import ProjectMember
from tests.integration.conftest import post_project, session_auth_headers

NIL_UUID = "00000000-0000-0000-0000-000000000000"
#: 库里不会有的收件箱行号。主键是 bigint 序列，不再是 uuid。
MISSING_ID = 9_999_999


def _create_project(client, name: str = "Demo") -> str:
    r = post_project(client, json={"name": name})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _roster(client, project_id: str, *handles: str) -> None:
    """把这几个人放上项目名册 —— 一条广播到得了谁手上，问的就是这份名册。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            for handle in handles:
                session.add(
                    ProjectMember(project_id=uuid.UUID(project_id), user_handle=handle)
                )
            await session.commit()

    asyncio.run(_run())


def _post_notif(client, project_id: str, **body) -> list[dict]:
    """写一条进去，返回它落成的那几行（广播是一人一行，所以这里是复数）。"""
    r = client.post(f"/projects/{project_id}/alerts", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _one(client, project_id: str, **body) -> dict:
    rows = _post_notif(client, project_id, **body)
    assert len(rows) == 1, rows
    return rows[0]


def _titles(client, project_id: str, handle: str | None = None, **params) -> list[str]:
    headers = session_auth_headers(handle) if handle else {}
    r = client.get(f"/projects/{project_id}/alerts", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return [n["title"] for n in r.json()["data"]["data"]]


def _inbox_titles(client, project_id: str, handle: str) -> list[str]:
    """收件箱里摆出来的那几条，新的在前。"""
    r = client.get(
        f"/projects/{project_id}/inbox", headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return [n["title"] for n in r.json()["data"]["data"]]


def test_create_notification(client):
    pid = _create_project(client)
    data = _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="文档更新了",
        body="改了三处",
        target_handle="alice",
        payload={"diff": 3},
    )
    assert data["level"] == "light"
    assert data["kind"] == "change_alert"
    assert data["title"] == "文档更新了"
    assert data["target_handle"] == "alice"
    assert data["payload"] == {"diff": 3}
    assert data["read"] is False


def test_create_notification_missing_project_404(client):
    r = client.post(
        f"/projects/{NIL_UUID}/alerts",
        json={"level": "silent", "kind": "change_alert", "title": "x"},
    )
    assert r.status_code == 404


def test_create_notification_invalid_level_rejected(client):
    pid = _create_project(client)
    r = client.post(
        f"/projects/{pid}/alerts",
        json={"level": "loud", "kind": "change_alert", "title": "x"},
    )
    # The merged app maps request-validation errors to 400 (知是 convention),
    # not FastAPI's default 422 (app/core/errors.validation_exception_handler).
    assert r.status_code == 400


def test_list_newest_first_and_filters(client):
    pid = _create_project(client)
    _roster(client, pid, "alice", "bob")
    _post_notif(client, pid, level="silent", kind="change_alert", title="第一条")
    _one(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="第二条",
        target_handle="bob",
    )
    _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="第三条",
        target_handle="alice",
    )

    # 没报身份的调用者没有收件箱，所以什么也看不到 —— 广播不再是一行谁都读得到的
    # 记录，它是名册上每个人各自的那一行。
    assert _titles(client, pid) == []

    # alice 看到自己的那两条（广播展开给她的那一行，加上点名给她的），看不到
    # bob 的。倒序。
    assert _titles(client, pid, "alice") == ["第三条", "第一条"]


def test_a_broadcast_lands_in_every_members_own_mailbox(client):
    pid = _create_project(client)
    _roster(client, pid, "alice", "bob")
    rows = _post_notif(
        client, pid, level="strong", kind="change_alert", title="全体注意"
    )
    # The project's owner is on its roster too (every project has one now).
    assert {row["target_handle"] for row in rows} == {"alice", "bob", "owner"}
    _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="给bob",
        target_handle="bob",
    )

    assert _titles(client, pid, "alice") == ["全体注意"]
    assert _titles(client, pid, "bob") == ["给bob", "全体注意"]


def test_list_unread_only(client):
    pid = _create_project(client)
    a = _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="A",
        target_handle="alice",
    )
    _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="B",
        target_handle="alice",
    )

    rr = client.post(f"/alerts/{a['id']}/read", headers=session_auth_headers("alice"))
    assert rr.status_code == 200

    assert _titles(client, pid, "alice", unread_only="true") == ["B"]


def _page(client, project_id: str, handle: str | None = None, **params) -> dict:
    """一次读的整份 payload：`data` / `total` / `has_more` / `next_start`。"""
    headers = session_auth_headers(handle) if handle else {}
    r = client.get(f"/projects/{project_id}/alerts", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_the_alert_list_is_paged_and_walks_to_the_end(client):
    """一页有上限；顺着 `next_start` 一路走到底，一行不漏、一行不重。

    这条读以前没有上界 —— 整个历史的信一次全给回来，一个项目跑久了响应只增不
    减。这里 25 条、一页 10 条，正好要翻三次。
    """
    pid = _create_project(client)
    for i in range(25):
        _one(
            client,
            pid,
            level="light",
            kind="change_alert",
            title=f"第 {i} 条",
            target_handle="alice",
        )

    first = _page(client, pid, "alice", page_size=10)
    # 一页只给 10 条，而 `total` 说的是真话：分页之前它写的是这一页的条数
    # （`page(items, len(items))`），那两个数在那时候永远相等。
    assert len(first["data"]) == 10
    assert first["total"] == 25
    assert first["has_more"] is True

    seen: list[int] = []
    page = first
    while True:
        seen.extend(n["id"] for n in page["data"])
        if not page["has_more"]:
            break
        assert page["next_start"] is not None
        # 游标原样喂回去 —— 它就是下一页的头一条，含它自己。
        page = _page(client, pid, "alice", page_size=10, page_start=page["next_start"])

    assert len(seen) == 25, seen  # 一条不漏
    assert len(set(seen)) == 25, seen  # 一条不重
    # 首页到最后：新的在前，整份读下来仍然有序。
    assert seen == sorted(seen, reverse=True)
    assert len(page["data"]) == 5
    assert page["next_start"] is None


def test_the_page_size_has_a_ceiling(client):
    """`page_size` 有上界，报不出来就 400（合并后的 app 把校验错映射成 400）。"""
    pid = _create_project(client)
    r = client.get(
        f"/projects/{pid}/alerts",
        headers=session_auth_headers("alice"),
        params={"page_size": 1000},
    )
    assert r.status_code == 400


def test_an_unknown_cursor_starts_from_the_top(client):
    """库里没有的游标从头开始，不报错 —— 过期的游标最多让人重看一遍第一页。"""
    pid = _create_project(client)
    for i in range(3):
        _one(
            client,
            pid,
            level="light",
            kind="change_alert",
            title=f"第 {i} 条",
            target_handle="alice",
        )

    page = _page(client, pid, "alice", page_size=2, page_start=MISSING_ID)
    assert [n["title"] for n in page["data"]] == ["第 2 条", "第 1 条"]
    assert page["total"] == 3


def test_inbox_carries_decisions_accepts_and_change_alerts(client):
    pid = _create_project(client)
    _roster(client, pid, "alice", "bob")
    # 进收件箱：还没读的变更提醒（spec §8.5 的第一种典型通知）。
    alert = _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="提醒",
        target_handle="alice",
    )
    # 不进收件箱：silent 的提醒（它的意思就是「记下来，别打扰」）。
    _one(
        client,
        pid,
        level="silent",
        kind="change_alert",
        title="安静的提醒",
        target_handle="alice",
    )
    # 进收件箱：decision_request + accept_request。
    decision = _one(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="拍板",
        target_handle="alice",
    )
    _one(
        client,
        pid,
        level="strong",
        kind="accept_request",
        title="验收",
        target_handle="bob",
    )

    # 决定进不进收件箱的是类别 + 读没读，不是发给谁：那两条提醒 alice 都收到了，
    # 收件箱里只多出「提醒」。
    assert _inbox_titles(client, pid, "alice") == ["拍板", "提醒"]
    assert _inbox_titles(client, pid, "bob") == ["验收"]

    # 变更提醒读过就收起来 —— 和验收卡同一条规矩（不是「拍板了才走」）。
    client.post(f"/alerts/{alert['id']}/read", headers=session_auth_headers("alice"))
    assert _inbox_titles(client, pid, "alice") == ["拍板"]

    # 决策请求被读过之后照样留在收件箱里 —— 拍板了才走。
    client.post(
        f"/alerts/{decision['id']}/read",
        headers=session_auth_headers("alice"),
    )
    assert _inbox_titles(client, pid, "alice") == ["拍板"]

    client.post(
        f"/alerts/{decision['id']}/resolve",
        json={"chosen": "随便"},
        headers=session_auth_headers("alice"),
    )
    assert _inbox_titles(client, pid, "alice") == []


def test_the_project_badge_counts_what_the_inbox_lists(client):
    """角标亮着的每一条，收件箱里都读得到。

    这就是 change_alert 曾经的那个缺陷：`unread_count_in_project` 把未读、非
    silent 的变更提醒数进了项目角标，而收件箱查询把它们滤掉了 —— 角标亮着，
    人进去一条也读不到，只能靠「全部已读」把它按掉。两边读的是同一批规则。
    """
    pid = _create_project(client)
    _roster(client, pid, "alice")
    _one(
        client,
        pid,
        level="strong",
        kind="change_alert",
        title="提醒",
        target_handle="alice",
    )
    _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="提醒二",
        target_handle="alice",
    )
    # silent 的两边都不算。
    _one(
        client,
        pid,
        level="silent",
        kind="change_alert",
        title="安静的",
        target_handle="alice",
    )

    badge = client.get(
        f"/projects/{pid}/alerts/unread-count", headers=session_auth_headers("alice")
    ).json()["data"]["unread"]
    listed = _inbox_titles(client, pid, "alice")
    assert badge == len(listed) == 2


def test_resolve_records_choice_and_posts_block(client):
    pid = _create_project(client)
    topic = client.post(
        "/topics", json={"project_id": pid, "title": "切分方案"}
    ).json()["data"]["id"]
    n = _one(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="评测集怎么切分",
        target_handle="user-1",
        topic_id=topic,
        payload={"options": ["按时间切分", "随机切分"]},
    )
    r = client.post(
        f"/alerts/{n['id']}/resolve",
        json={"chosen": "按时间切分"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["resolved_at"] is not None
    assert data["payload"]["resolved_choice"] == "按时间切分"
    # 决定丢回房间，芝士下一轮读得到，署的是验证过的调用者，不是 body 说的名字。
    blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    decision_block = next(b for b in blocks if "按时间切分" in b["content"])
    assert decision_block["author"] == "user-1"
    # 不在候选里的选项要被拒。
    n2 = _one(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="再来一个",
        target_handle="user-1",
        payload={"options": ["A", "B"]},
    )
    bad = client.post(
        f"/alerts/{n2['id']}/resolve",
        json={"chosen": "C"},
        headers=session_auth_headers("user-1"),
    )
    assert bad.status_code == 422


def test_mark_read_flips_read(client):
    pid = _create_project(client)
    n = _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="X",
        target_handle="user-1",
    )
    assert n["read"] is False

    r = client.post(f"/alerts/{n['id']}/read", headers=session_auth_headers("user-1"))
    assert r.status_code == 200
    assert r.json()["data"]["read"] is True


def test_mark_read_missing_404(client):
    r = client.post(f"/alerts/{MISSING_ID}/read")
    assert r.status_code == 404
