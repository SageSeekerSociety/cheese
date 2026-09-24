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
    assert data["feedback"] is None


def test_create_notification_missing_project_404(client):
    r = client.post(
        f"/projects/{NIL_UUID}/alerts",
        json={"level": "silent", "kind": "heartbeat", "title": "x"},
    )
    assert r.status_code == 404


def test_create_notification_invalid_level_rejected(client):
    pid = _create_project(client)
    r = client.post(
        f"/projects/{pid}/alerts",
        json={"level": "loud", "kind": "heartbeat", "title": "x"},
    )
    # The merged app maps request-validation errors to 400 (知是 convention),
    # not FastAPI's default 422 (app/core/errors.validation_exception_handler).
    assert r.status_code == 400


def test_list_newest_first_and_filters(client):
    pid = _create_project(client)
    _roster(client, pid, "alice", "bob")
    _post_notif(client, pid, level="silent", kind="heartbeat", title="第一条")
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


def test_inbox_only_unread_decision_and_accept(client):
    pid = _create_project(client)
    _roster(client, pid, "alice", "bob")
    # 不进收件箱：change_alert + heartbeat。
    _post_notif(client, pid, level="light", kind="change_alert", title="alert")
    _post_notif(client, pid, level="silent", kind="heartbeat", title="beat")
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

    # 决定进不进收件箱的是类别，不是发给谁：那条提醒和那次巡检两个人都收到了，
    # 而谁的收件箱里都没有它们。
    alice = client.get(
        f"/projects/{pid}/inbox", headers=session_auth_headers("alice")
    ).json()["data"]
    bob = client.get(
        f"/projects/{pid}/inbox", headers=session_auth_headers("bob")
    ).json()["data"]
    assert [n["title"] for n in alice["data"]] == ["拍板"]
    assert [n["title"] for n in bob["data"]] == ["验收"]
    assert {n["kind"] for n in alice["data"] + bob["data"]} == {
        "decision_request",
        "accept_request",
    }

    # 决策请求被读过之后照样留在收件箱里 —— 拍板了才走。
    client.post(
        f"/alerts/{decision['id']}/read",
        headers=session_auth_headers("alice"),
    )
    r = client.get(f"/projects/{pid}/inbox", headers=session_auth_headers("alice"))
    assert r.json()["data"]["total"] == 1

    client.post(
        f"/alerts/{decision['id']}/resolve",
        json={"chosen": "随便"},
        headers=session_auth_headers("alice"),
    )
    r = client.get(f"/projects/{pid}/inbox", headers=session_auth_headers("alice"))
    assert r.json()["data"]["total"] == 0


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


def test_feedback_up_and_down(client):
    pid = _create_project(client)
    n = _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="X",
        target_handle="user-1",
    )
    headers = session_auth_headers("user-1")

    r = client.post(
        f"/alerts/{n['id']}/feedback",
        json={"feedback": "up"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["feedback"] == "up"

    r = client.post(
        f"/alerts/{n['id']}/feedback",
        json={"feedback": "down"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["feedback"] == "down"


def test_feedback_invalid_422(client):
    pid = _create_project(client)
    n = _one(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="X",
        target_handle="user-1",
    )
    r = client.post(
        f"/alerts/{n['id']}/feedback",
        json={"feedback": "meh"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 422


def test_feedback_missing_404(client):
    r = client.post(f"/alerts/{MISSING_ID}/feedback", json={"feedback": "up"})
    assert r.status_code == 404
