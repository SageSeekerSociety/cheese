"""两张通知表并成一张：存量的 `alerts` 搬进 `notification`（迁移 c8d3a1e07f54）。

判据落在**读得出来的那一头**：搬完之后，同一个收件人从项目收件箱里看到的条数和
内容，和他在 `alerts` 那张表上本该看到的一样。`alerts` 的可见规则只有一句 ——
「点名给我的，加上谁都看得见的那些」—— 所以这里把它写成期望表，让新的读路径去对。

广播是这次搬家唯一改形状的地方：`alerts` 一行 `target_handle IS NULL` 表示「谁都
看得见」，而并起来的那张表一行只对一个收件人。所以一条广播搬完之后，当时房间里
每个人各一行 —— 不是一行。agent 不在里面（`identity/arrival.py`：它在自己房间的
时间线上读到这件事）。

跑的是**迁移的 `upgrade()` 本身**：把迁移模块加载进来，配一个真的 alembic
operations 上下文再调它，跟 `alembic upgrade head` 走同一条路。不抽一个函数出来
绕开 alembic —— 那样「`upgrade()` 确实调了搬家」就没人盯着。
"""

import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.integration.conftest import session_auth_headers

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c8d3a1e07f54_two_notification_tables_become_one.py"
)

#: 房间里的 agent。广播不展开到它头上。
AGENT = "cheese-0123456789ab"


def _load():
    spec = importlib.util.spec_from_file_location("_mig_two_tables_one", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(client) -> None:
    """把迁移的 `upgrade()` 跑一遍，跟 `alembic upgrade head` 同一条路。"""

    def _apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            _load().upgrade()

    async def _run() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())


def _room(client) -> tuple[str, str]:
    """一个项目 + 一个房间，名册上是 alice、bob 和一个 agent。"""
    pid = client.post("/projects", json={"name": "并表"}).json()["data"]["id"]
    tid = client.post(
        "/topics", json={"project_id": pid, "title": "房间", "created_by": "alice"}
    ).json()["data"]["id"]

    async def _run() -> None:
        async with client.test_factory() as s:
            for handle in ("alice", "bob"):
                await s.execute(
                    text(
                        "INSERT INTO project_members"
                        " (id, project_id, user_handle, role, created_at, updated_at)"
                        " VALUES (:id, :pid, :handle, 'member', :now, :now)"
                        " ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "pid": uuid.UUID(pid),
                        "handle": handle,
                        "now": datetime.now(UTC),
                    },
                )
            for handle in ("alice", "bob", AGENT):
                await s.execute(
                    text(
                        "INSERT INTO topic_memberships"
                        " (id, topic_id, member_handle, role, created_at, updated_at)"
                        " VALUES (:id, :tid, :handle, 'member', :now, :now)"
                        " ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tid": uuid.UUID(tid),
                        "handle": handle,
                        "now": datetime.now(UTC),
                    },
                )
            await s.commit()

    asyncio.run(_run())
    return pid, tid


def _seed_alerts(client, pid: str, tid: str, rows: list[dict]) -> None:
    """往退役的那张表里铺行 —— 它已经没有 ORM 模型了，正是搬家要清空的那一张。"""

    async def _run() -> None:
        async with client.test_factory() as s:
            base = datetime.now(UTC)
            for i, row in enumerate(rows):
                await s.execute(
                    text(
                        "INSERT INTO alerts (id, project_id, topic_id, level, kind,"
                        " target_handle, title, body, payload, read_at, resolved_at,"
                        " feedback, created_at, updated_at)"
                        " VALUES (:id, :pid, :tid, :level, :kind, :target, :title,"
                        " :body, CAST(:payload AS json), :read_at, NULL, NULL,"
                        " :now, :now)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "pid": uuid.UUID(pid),
                        "tid": uuid.UUID(tid) if row.get("topic") else None,
                        "level": row.get("level", "light"),
                        "kind": row.get("kind", "change_alert"),
                        "target": row.get("target"),
                        "title": row["title"],
                        "body": row.get("body", ""),
                        "payload": json.dumps(row.get("payload", {})),
                        "read_at": base if row.get("read") else None,
                        # 搬完按 created_at 倒序读，所以铺的时候拉开先后。
                        "now": base - timedelta(minutes=len(rows) - i),
                    },
                )
            await s.commit()

    asyncio.run(_run())


def _inbox(client, pid: str, handle: str) -> list[dict]:
    r = client.get(f"/projects/{pid}/alerts", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


SEEDED = [
    {
        "target": "alice",
        "title": "给alice",
        "kind": "decision_request",
        "level": "strong",
        "payload": {"options": ["A", "B"]},
        "topic": True,
    },
    {"target": "bob", "title": "给bob", "read": True},
    {"target": None, "title": "全体注意", "topic": True},
    {"target": AGENT, "title": "点名给芝士", "topic": True},
]

#: `alerts` 的可见规则：点名给我的，加上谁都看得见的那些。倒序按 created_at。
VISIBLE = {
    "alice": ["全体注意", "给alice"],
    "bob": ["全体注意", "给bob"],
}


def test_every_recipient_sees_the_same_rows_after_the_move(client):
    pid, tid = _room(client)
    _seed_alerts(client, pid, tid, SEEDED)

    _upgrade(client)

    for handle, titles in VISIBLE.items():
        assert [row["title"] for row in _inbox(client, pid, handle)] == titles, handle
    # 点名给 agent 的那一条照搬，收件人还是它自己。
    assert _recipients_of(client, "点名给芝士") == {AGENT}


def test_a_broadcast_becomes_one_row_per_person_in_the_room(client):
    """一条广播搬完之后，当时房间里每个人各一行 —— 不是一行谁都看得见。"""
    pid, tid = _room(client)
    broadcast = {"target": None, "title": "全体注意", "topic": True}
    _seed_alerts(client, pid, tid, [broadcast])

    _upgrade(client)

    recipients = _recipients_of(client, "全体注意")
    assert recipients == {"alice", "bob"}, recipients


def test_the_moved_rows_keep_what_they_said(client):
    """内容一致：等级、类别、选项、读没读，一条不落。"""
    pid, tid = _room(client)
    _seed_alerts(client, pid, tid, SEEDED)

    _upgrade(client)

    alice = {row["title"]: row for row in _inbox(client, pid, "alice")}
    decision = alice["给alice"]
    assert decision["kind"] == "decision_request"
    assert decision["level"] == "strong"
    assert decision["payload"]["options"] == ["A", "B"]
    assert decision["read"] is False
    assert decision["topic_id"] == tid
    assert isinstance(decision["id"], int)

    bob = {row["title"]: row for row in _inbox(client, pid, "bob")}
    assert bob["给bob"]["read"] is True


def test_running_the_move_twice_adds_nothing(client):
    """幂等：`DROP TABLE` 那一条要在删表之前原样再跑一遍这次搬家。"""
    pid, tid = _room(client)
    _seed_alerts(client, pid, tid, SEEDED)

    _upgrade(client)
    first = {h: _inbox(client, pid, h) for h in ("alice", "bob")}
    _upgrade(client)
    again = {h: _inbox(client, pid, h) for h in ("alice", "bob")}

    assert {h: [r["id"] for r in rows] for h, rows in first.items()} == {
        h: [r["id"] for r in rows] for h, rows in again.items()
    }


def test_the_move_finds_the_live_account_not_a_retired_one(client):
    """搬过来的行挂在**活着**的那个账号上。

    `user.username` 上没有唯一约束，所以一个 handle 注销之后被重新注册就是两行。
    按 id 取最老的一行正好取中那个死账号 —— 这条通知于是永远不出现在真人的站内信
    里。服务侧的判据（`UserRepository.get_by_username`）挡了注销账号，搬家那一句
    要挡同一个，否则两边认的不是同一个人。
    """
    pid, tid = _room(client)
    retired, live = _two_accounts_named(client, "alice")
    _seed_alerts(
        client, pid, tid, [{"target": "alice", "title": "给alice", "topic": True}]
    )

    _upgrade(client)

    assert _receivers_of(client, "给alice") == {live}, retired


def test_rolling_back_does_not_invent_an_account_for_the_rows_it_keeps(client):
    """回滚把收件人为空的行删掉，而不是编一个 0 号用户。

    这些行不只是搬家搬来的：handle 在账号池里没有对应行时新代码本来就这么写，
    @ 一个 agent 就产生一条，而 `DELETE ... delivery_key LIKE 'alert:%'` 删不掉
    它们。给它们编一个不存在的账号 id，回滚之后它们就停在那个谁也打不开的信箱
    里，而按 `receiver_id` 数未读的那一侧认得它们。
    """
    pid, tid = _room(client)
    # 点名给 agent 的那一条：`cheese-…` 在账号池里没有行，所以 `receiver_id` 为空。
    _seed_alerts(
        client, pid, tid, [{"target": AGENT, "title": "点名给芝士", "topic": True}]
    )
    _upgrade(client)
    orphans = _ids_without_an_account(client)
    assert orphans, "这一步该留下一条收件人为空的行，否则下面测不到东西"

    _downgrade(client)

    assert _count_where(client, "receiver_id = 0") == 0
    assert _count_where(client, f"id IN ({','.join(str(i) for i in orphans)})") == 0


def _recipients_of(client, title: str) -> set[str]:
    """这条通知在收件箱里落到了谁头上。"""

    async def _run() -> set[str]:
        async with client.test_factory() as s:
            rows = await s.execute(
                text("SELECT recipient_handle FROM notification WHERE title = :title"),
                {"title": title},
            )
            return {row[0] for row in rows.all()}

    return asyncio.run(_run())


def _receivers_of(client, title: str) -> set[int | None]:
    """这条通知挂在账号池里的哪一行上。"""

    async def _run() -> set[int | None]:
        async with client.test_factory() as s:
            rows = await s.execute(
                text("SELECT receiver_id FROM notification WHERE title = :title"),
                {"title": title},
            )
            return {row[0] for row in rows.all()}

    return asyncio.run(_run())


def _two_accounts_named(client, handle: str) -> tuple[int, int]:
    """同一个 handle 的两行：先注销的那一行，和现在活着的那一行。"""

    async def _run() -> tuple[int, int]:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            ids = []
            for deleted_at in (now, None):
                row = await s.execute(
                    text(
                        'INSERT INTO "user"'
                        " (username, email, created_at, updated_at, deleted_at)"
                        " VALUES (:h, :email, :now, :now, :deleted_at)"
                        " RETURNING id"
                    ),
                    {
                        "h": handle,
                        "email": f"{uuid.uuid4().hex}@example.com",
                        "now": now,
                        "deleted_at": deleted_at,
                    },
                )
                ids.append(int(row.scalar_one()))
            await s.commit()
            return ids[0], ids[1]

    return asyncio.run(_run())


def _downgrade(client) -> None:
    def _apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            _load().downgrade()

    async def _run() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())


def _ids_without_an_account(client) -> list[int]:
    async def _run() -> list[int]:
        async with client.test_factory() as s:
            rows = await s.execute(
                text("SELECT id FROM notification WHERE receiver_id IS NULL")
            )
            return [int(row[0]) for row in rows.all()]

    return asyncio.run(_run())


def _count_where(client, predicate: str) -> int:
    async def _run() -> int:
        async with client.test_factory() as s:
            return int(
                (
                    await s.execute(
                        text(f"SELECT count(*) FROM notification WHERE {predicate}")
                    )
                ).scalar_one()
            )

    return asyncio.run(_run())
