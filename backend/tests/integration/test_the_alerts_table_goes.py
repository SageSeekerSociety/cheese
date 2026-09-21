"""`alerts` 退场：行落过两遍之后，表才删（迁移 d3f0a91c7b45）。

上一次部署（`c8d3a1e07f54`）把 `alerts` 的行搬进了 `notification`，表留着 —— 换
镜像那段窗口里旧镜像还在往 `alerts` 里写，那一批第一遍搬家扫不到。这条迁移是第二
遍：再搬一次接住它们，逐行核对每一条都在 `notification` 里有同一份内容，然后才
`DROP TABLE`。

三条判据，对应迁移的三步：

1. **搬家前后同一个收件人看到的条数与内容一致** —— 上一次已经到手的一条不少、
   一字不差，窗口里写的那一条这一次到了。
2. **对不上就停在那儿，不是把表删掉** —— 造一条搬完之后被改过的 alert，迁移抛错
   点名它，表和行原样留着，收件箱一行没动。
3. **一个收件人也算不出来的广播不算对不上** —— 它谁也没送到过，删表之后照旧没人
   读得到，所以写进迁移日志、不挡删表；这一行随表一起消失，日志里连项目、房间、
   标题、写下的时刻一起留着，事后拿一个 uuid 是查不回来的。

跑的是**迁移的 `upgrade()` 本身**，跟 `alembic upgrade head` 走同一条路；`alerts`
在 head 上已经没有了，所以表由 `create_alerts_table()` 立起来（
`test_alerts_move_into_the_one_notification_table.py`），它也是上一条迁移那几个
用例现在用的同一份。
"""

import asyncio
import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

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
