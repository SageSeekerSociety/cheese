"""`alerts` 退场：行落过两遍之后，表才删（迁移 d3f0a91c7b45）。

上一次部署（`c8d3a1e07f54`）把 `alerts` 的行搬进了 `notification`，表留着 —— 换
镜像那段窗口里旧镜像还在往 `alerts` 里写，那一批第一遍搬家扫不到。这条迁移是第二
遍：再搬一次接住它们，逐行核对每一条都在 `notification` 里有同一份内容，然后才
`DROP TABLE`。

五条判据，对应迁移的四步 —— 第 2 步占两条，它合过来的方向和它不盖的方向各一条：

1. **搬家前后同一个收件人看到的条数与内容一致** —— 上一次已经到手的一条不少、
   一字不差，窗口里写的那一条这一次到了。
2. **窗口里留在已经搬过的那些行上的读、拍板、反馈，跟着过来** —— 那批行第一遍
   搬过，所以第二遍按原 uuid 整条跳过；跳过就等于把那几笔留在原表上跟着 `DROP
   TABLE` 一起没，而一条拍过板的决策请求会因此回到收件箱里显示待答，被再拍一次。
3. **合过来只填不盖** —— 旧行冻在搬家那一刻，而换完镜像之后人在收件箱里拍的板、
   按的赞落的都是新那一行；拿旧行去盖新的，擦掉的是正常使用，代价和第 2 条一模
   一样（回到待答、被再拍一次、房间里第二条【决策】块）。
4. **对不上就停在那儿，不是把表删掉** —— 造一条搬完之后被改过的 alert，迁移抛错
   点名它，表和行原样留着，收件箱一行没动。
5. **一个收件人也算不出来的广播不算对不上** —— 它谁也没送到过，删表之后照旧没人
   读得到，所以写进迁移日志、不挡删表；这一行随表一起消失，日志里连项目、房间、
   标题、写下的时刻一起留着，事后拿一个 uuid 是查不回来的。

跑的是**迁移的 `upgrade()` 本身**，跟 `alembic upgrade head` 走同一条路；`alerts`
在 head 上已经没有了，所以表由 `create_alerts_table()` 立起来（
`test_alerts_move_into_the_one_notification_table.py`），它也是上一条迁移那几个
用例现在用的同一份。
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration.conftest import session_auth_headers
from tests.integration.test_alerts_move_into_the_one_notification_table import (
    _inbox,
    _recipients_of,
    _room,
    _seed_alerts,
    _upgrade,
    create_alerts_table,
)

DROP_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d3f0a91c7b45_the_alerts_table_goes.py"
)

#: 上一次部署之前就在 `alerts` 里的那几条。
SEEDED = [
    {
        "target": "alice",
        "title": "给alice",
        "kind": "decision_request",
        "level": "strong",
        "payload": {"options": ["A", "B"]},
        "topic": True,
    },
    {"target": "bob", "title": "给bob", "read": True, "topic": True},
    {"target": None, "title": "全体注意", "topic": True},
]

#: 窗口里旧镜像写下的那一条：第一遍搬家扫不到它，这一遍才到。
WINDOW = {"target": None, "title": "窗口里写的", "topic": True}


def test_the_window_row_lands_and_then_the_table_goes(client):
    """搬家前后同一个收件人看到的条数与内容一致，窗口里那一条这一次到了。"""
    pid, tid = _room(client)
    _seed_alerts(client, pid, tid, SEEDED)
    _upgrade(client)  # 上一次部署：第一遍搬家
    before = {handle: _inbox(client, pid, handle) for handle in ("alice", "bob")}
    _seed_alerts(client, pid, tid, [WINDOW])  # 旧镜像还在往 `alerts` 里写

    _upgrade(client, DROP_MIGRATION)

    for handle, rows in before.items():
        after = _inbox(client, pid, handle)
        kept = [row for row in after if row["id"] in {r["id"] for r in rows}]
        assert kept == rows, handle
        assert after[0]["title"] == "窗口里写的", handle
        assert len(after) == len(rows) + 1, handle
    assert not _alerts_exists(client)


def test_what_the_window_left_on_an_already_moved_row_comes_over(client):
    """窗口里旧镜像在**已经搬过**的行上拍的板、按的赞、读掉的那一条，跟着过来。

    这批行第一遍搬家搬过了，所以第二遍按原 uuid 整条跳过，核对那一步又只比内容
    不比状态 —— 两道都拦不住，删表就把它们带走了。代价不是少一个已读标记：那条
    决策请求在新那一侧 `resolved_at` 还是空的，于是它回到「等你处理的事」里显示
    待答，人再拍一次板，房间里落第二条【决策】块。
    """
    pid, tid = _room(client)
    _seed_alerts(
        client,
        pid,
        tid,
        [
            {
                "target": "alice",
                "title": "要不要上",
                "kind": "decision_request",
                "level": "strong",
                "payload": {"options": ["上", "再等等"]},
                "topic": True,
            },
            {"target": "bob", "title": "看一眼", "topic": True},
        ],
    )
    _upgrade(client)  # 上一次部署：第一遍搬家，这两条都搬过去了
    # 窗口里旧镜像还在 `alerts` 上服务 `/alerts/{id}/resolve` 与 `/feedback`
    _the_old_image_writes(
        client,
        "要不要上",
        resolved={"options": ["上", "再等等"], "resolved_choice": "上"},
    )
    _the_old_image_writes(client, "看一眼", feedback="up")

    _upgrade(client, DROP_MIGRATION)

    decided = {row["title"]: row for row in _inbox(client, pid, "alice")}["要不要上"]
    assert decided["resolved_at"] is not None
    assert decided["payload"]["resolved_choice"] == "上"
    assert decided["read"] is True
    # 拍过板的不再挂在「等你处理的事」里 —— 挂着就会被再拍一次
    assert "要不要上" not in [row["title"] for row in _pending(client, pid, "alice")]

    seen = {row["title"]: row for row in _inbox(client, pid, "bob")}["看一眼"]
    assert seen["feedback"] == "up"
    assert not _alerts_exists(client)


def test_what_the_person_did_in_the_new_inbox_survives(client):
    """第 2 步**只填不盖**：换完镜像之后人在收件箱里拍的板、按的赞，旧行盖不掉。

    第 2 步合过来的是**旧行**上的读、拍板、反馈，而旧行冻在第一遍搬家那一刻 ——
    把它写成直接覆盖（`a.resolved_at` 而不是 `COALESCE(n.resolved_at, ...)`），
    擦掉的正是「两次部署之间有人用过收件箱」这件正常的事，而代价和上一条一模一样：
    那条决策请求 `resolved_at` 回到空，于是回到「等你处理的事」里显示待答，人再拍
    一次板，房间里落第二条【决策】块。

    所以这两行旧那一侧只带「读过」和一个旧反馈 —— 带一样状态才进得了第 2 步的
    WHERE，进不去的行这一步本来就不碰 —— 拍板、新反馈都发生在新那一侧。
    """
    pid, tid = _room(client)
    _seed_alerts(
        client,
        pid,
        tid,
        [
            {
                "target": "alice",
                "title": "要不要上",
                "kind": "decision_request",
                "level": "strong",
                "payload": {"options": ["上", "再等等"]},
                "topic": True,
            },
            {"target": "bob", "title": "看一眼", "topic": True},
        ],
    )
    _upgrade(client)  # 上一次部署：第一遍搬家，这两条都搬过去了
    # 窗口里旧镜像在旧行上留下的：一个已读，一个赞。拍板和新反馈还没发生。
    _the_old_image_writes(client, "要不要上", read=True)
    _the_old_image_writes(client, "看一眼", feedback="up")

    # 换完镜像，收件箱就是 `notification` 了 —— 人动的是新那一行（bigint id）。
    alice = {row["title"]: row for row in _inbox(client, pid, "alice")}
    r = client.post(
        f"/alerts/{alice['要不要上']['id']}/resolve",
        json={"chosen": "上"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    bob = {row["title"]: row for row in _inbox(client, pid, "bob")}
    r = client.post(
        f"/alerts/{bob['看一眼']['id']}/read", headers=session_auth_headers("bob")
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/alerts/{bob['看一眼']['id']}/feedback",
        json={"feedback": "down"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200, r.text

    decided = {row["title"]: row for row in _inbox(client, pid, "alice")}["要不要上"]
    reacted = {row["title"]: row for row in _inbox(client, pid, "bob")}["看一眼"]
    assert decided["resolved_at"] is not None
    assert decided["payload"]["resolved_choice"] == "上"
    # 新那一侧按的是「down」，旧行上冻着的是「up」；旧行的 `read_at` 是空的
    assert (reacted["feedback"], reacted["read"]) == ("down", True)

    _upgrade(client, DROP_MIGRATION)

    after_alice = {row["title"]: row for row in _inbox(client, pid, "alice")}
    after_bob = {row["title"]: row for row in _inbox(client, pid, "bob")}
    assert after_alice["要不要上"] == decided  # 拍板、选的那一项，一字没动
    assert after_bob["看一眼"] == reacted  # 赞和已读，一字没动
    # 拍过板的不回到「等你处理的事」里 —— 回去就会被再拍一次
    assert "要不要上" not in [row["title"] for row in _pending(client, pid, "alice")]
    assert not _alerts_exists(client)


def test_a_row_that_does_not_line_up_stops_the_migration(client):
    """搬完之后被改过的那一条：迁移停在那儿，表和行原样留着。"""
    pid, tid = _room(client)
    _seed_alerts(
        client, pid, tid, [{"target": "alice", "title": "给alice", "topic": True}]
    )
    _upgrade(client)
    inbox = _inbox(client, pid, "alice")
    changed = _rewrite_body(client, "给alice", "窗口里旧镜像改过的正文")

    with pytest.raises(RuntimeError) as err:
        _upgrade(client, DROP_MIGRATION)

    assert str(changed) in str(err.value)  # 点名是哪一条
    assert _alerts_exists(client)
    assert _alert_ids(client) == [changed]
    assert _inbox(client, pid, "alice") == inbox


def test_a_broadcast_that_reached_nobody_does_not_stop_the_drop(client, caplog):
    """空名册上的一条广播：谁也没收到过，写进日志，不挡删表。

    放行就是跟着 `DROP TABLE` 一起删掉，事后拿着 id 再也查不回来 —— 所以日志行
    自己要说得清这条广播是什么：项目、房间、标题、写下的时刻。
    """
    pid, tid = _empty_room(client)
    _seed_alerts(
        client, pid, tid, [{"target": None, "title": "没人在的房间", "topic": True}]
    )
    alert = _alert_ids(client)[0]

    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        _upgrade(client)
        _upgrade(client, DROP_MIGRATION)

    assert str(alert) in caplog.text
    for said in (pid, tid, "没人在的房间"):
        assert said in caplog.text
    assert not _alerts_exists(client)
    assert _recipients_of(client, "没人在的房间") == set()


def _empty_room(client) -> tuple[str, str]:
    """一个项目 + 一个名册上一个人都没有的房间。

    建房间这条路自己会把创建者放上名册，所以清一遍 —— 要的是「广播发出去的时候
    房间里没有人」这个状态本身。
    """
    create_alerts_table(client)
    pid = client.post("/projects", json={"name": "没人在"}).json()["data"]["id"]
    tid = client.post(
        "/topics", json={"project_id": pid, "title": "空房间", "created_by": "alice"}
    ).json()["data"]["id"]

    async def _run() -> None:
        async with client.test_factory() as s:
            await s.execute(
                text("DELETE FROM topic_memberships WHERE topic_id = :tid"),
                {"tid": uuid.UUID(tid)},
            )
            await s.execute(
                text("DELETE FROM project_members WHERE project_id = :pid"),
                {"pid": uuid.UUID(pid)},
            )
            await s.commit()

    asyncio.run(_run())
    return pid, tid


def _pending(client, pid: str, handle: str) -> list[dict]:
    """「等你处理的事」：决策请求挂到拍板为止。"""
    r = client.get(f"/projects/{pid}/inbox", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _the_old_image_writes(
    client,
    title: str,
    *,
    resolved: dict | None = None,
    feedback: str | None = None,
    read: bool = False,
) -> None:
    """旧镜像在窗口里动了这条 alert —— 拍板写 `resolved_at` 加 `read_at` 加
    `payload.resolved_choice`（并往房间里丢一条【决策】），按赞写 `feedback`，
    读掉写 `read_at`。搬过去的那一份不知道。"""

    async def _run() -> None:
        async with client.test_factory() as s:
            if read:
                await s.execute(
                    text(
                        "UPDATE alerts SET read_at = coalesce(read_at, now())"
                        " WHERE title = :title"
                    ),
                    {"title": title},
                )
            if resolved is not None:
                await s.execute(
                    text(
                        "UPDATE alerts SET resolved_at = now(),"
                        " read_at = coalesce(read_at, now()),"
                        " payload = CAST(:payload AS json)"
                        " WHERE title = :title"
                    ),
                    {"payload": json.dumps(resolved), "title": title},
                )
            if feedback is not None:
                await s.execute(
                    text("UPDATE alerts SET feedback = :f WHERE title = :title"),
                    {"f": feedback, "title": title},
                )
            await s.commit()

    asyncio.run(_run())


def _rewrite_body(client, title: str, body: str) -> uuid.UUID:
    """旧镜像在搬完之后动了这条 alert 的正文 —— 搬过去的那一份不知道。"""

    async def _run() -> uuid.UUID:
        async with client.test_factory() as s:
            row = await s.execute(
                text(
                    "UPDATE alerts SET body = :body WHERE title = :title RETURNING id"
                ),
                {"body": body, "title": title},
            )
            changed = row.scalar_one()
            await s.commit()
            return changed

    return asyncio.run(_run())


def _alert_ids(client) -> list[uuid.UUID]:
    async def _run() -> list[uuid.UUID]:
        async with client.test_factory() as s:
            rows = await s.execute(text("SELECT id FROM alerts ORDER BY created_at"))
            return [row[0] for row in rows.all()]

    return asyncio.run(_run())


def _alerts_exists(client) -> bool:
    async def _run() -> bool:
        async with client.test_factory() as s:
            row = await s.execute(text("SELECT to_regclass('public.alerts')"))
            return row.scalar_one() is not None

    return asyncio.run(_run())
