"""Functional tests for the flat ``/notifications`` inbox.

The shape tests in ``test_notifications_contract.py`` only exercise the empty
inbox. These seed real ``Notification`` rows (via the client's isolated
per-worker ``test_factory``, on the same DB the app reads through the overridden
``get_db``) and drive the full read → mark-read → delete lifecycle plus cursor
pagination, so a regression in the wiring — not just the response envelope — is
caught.

All rows are addressed to the seeded platform agent user (id=1, "cheese") that
``authed_client`` authenticates as.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.domain.notification.models import Notification, NotificationType
from tests.integration.conftest import a_team

_AGENT_USER_ID = 1
#: `authed_client` 就是这个人（平台 agent 用户，见 contract/conftest.py）。
_AGENT_HANDLE = "cheese"


async def _seed(
    factory,
    *,
    read: bool = False,
    finalized: bool = True,
    type_: NotificationType = NotificationType.MENTION,
    created_at: datetime | None = None,
    metadata: dict | None = None,
) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        row = Notification(
            receiver_id=_AGENT_USER_ID,
            type=type_,
            read=read,
            finalized=finalized,
            metadata_payload=metadata or {},
            created_at=created_at or now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        row_id = row.id
        await session.commit()
    return row_id


@pytest.mark.anyio
async def test_lifecycle_read_and_delete(authed_client: AsyncClient) -> None:
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    id1 = await _seed(factory)
    id2 = await _seed(factory)

    # Two unread in the inbox.
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 2

    resp = await authed_client.get("/notifications", params={"pageSize": 10})
    data = resp.json()["data"]
    assert {n["id"] for n in data["notifications"]} == {id1, id2}
    assert all(n["read"] is False for n in data["notifications"])
    assert data["page"]["total"] == 2
    assert data["page"]["hasMore"] is False

    # Mark one read → it flips, unread count drops.
    resp = await authed_client.patch(f"/notifications/{id1}", json={"read": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["notification"]["read"] is True
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 1

    # And back to unread: the single-notification route takes false, unlike the
    # collective one, so a reader who marked something read by mistake can undo it.
    resp = await authed_client.patch(f"/notifications/{id1}", json={"read": False})
    assert resp.status_code == 200
    assert resp.json()["data"]["notification"]["read"] is False
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 2
    resp = await authed_client.patch(f"/notifications/{id1}", json={"read": True})
    assert resp.status_code == 200

    # Collective mark-all-read clears the remaining one.
    resp = await authed_client.put("/notifications/status", json={"read": True})
    assert resp.json()["data"]["count"] == 1
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 0

    # Delete → gone (204), then 404 on re-fetch; the sibling survives.
    resp = await authed_client.delete(f"/notifications/{id1}")
    assert resp.status_code == 204
    resp = await authed_client.get(f"/notifications/{id1}")
    assert resp.status_code == 404
    resp = await authed_client.get(f"/notifications/{id2}")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_a_project_notification_never_reaches_the_flat_inbox(
    authed_client: AsyncClient,
) -> None:
    """项目收件箱的那一行不进知是的铃铛 —— 哪怕收件人就是这个登录用户。

    两张通知表并成一张之后，房间里的一次 @ 和一封站内信是同一张表的两行。这一侧
    渲染不了前者：它按 ``type`` 找模板，而平台报告自己的那几种（`change_alert`、
    `decision_request`、房间里的 `MENTION`）文字在 ``title``/``body`` 上，模板要
    的 ``payload`` 里空空如也 —— 铃铛里会多出一排读不出内容的空壳，未读数照加，
    再点一次「全部已读」，项目那边没读的也跟着被抹掉。
    """
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    mine = await _seed(factory)

    pid = await _seed_project(factory)
    posted = await authed_client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "strong",
            "kind": "MENTION",
            "title": "在房间里@了你",
            "body": "看一眼",
            "target_handle": _AGENT_HANDLE,
        },
    )
    assert posted.status_code == 200, posted.text

    # 铃铛里只有站内信那一条。
    listed = await authed_client.get("/notifications", params={"pageSize": 10})
    data = listed.json()["data"]
    assert [n["id"] for n in data["notifications"]] == [mine]
    assert data["page"]["total"] == 1
    unread = await authed_client.get("/notifications/unread-count")
    assert unread.json()["data"]["count"] == 1

    # 「全部已读」也够不着它：项目那边的未读不该被这一侧一键清掉。
    await authed_client.put("/notifications/status", json={"read": True})
    inbox = await authed_client.get(
        f"/projects/{pid}/alerts", params={"target_handle": _AGENT_HANDLE}
    )
    assert [row["read"] for row in inbox.json()["data"]["data"]] == [False]


@pytest.mark.anyio
async def test_collective_status_refuses_to_unread_everything(
    authed_client: AsyncClient,
) -> None:
    """``PUT /notifications/status`` marks all read and nothing else.

    The collective route takes a boolean, and the only value it accepts is
    ``true``: there is no "mark my whole inbox unread" operation, and a client
    that sends ``false`` must be told so rather than have the request silently
    do nothing or, worse, turn every read notification back to unread.
    """
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    read_already = await _seed(factory, read=True)
    await _seed(factory, read=False)

    resp = await authed_client.put("/notifications/status", json={"read": False})
    assert resp.status_code == 400

    # The inbox is exactly as it was: one unread, and the read one still read.
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 1
    resp = await authed_client.get(f"/notifications/{read_already}")
    assert resp.json()["data"]["notification"]["read"] is True


@pytest.mark.anyio
async def test_cursor_pagination_round_trip(authed_client: AsyncClient) -> None:
    """Seed 3 rows with distinct timestamps and page through them 2-at-a-time,
    round-tripping the opaque ``nextStart`` cursor back into ``pageStart``."""
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    base = datetime.now(UTC)
    # Newest-first ordering: newer created_at comes first.
    oldest = await _seed(factory, created_at=base - timedelta(minutes=3))
    middle = await _seed(factory, created_at=base - timedelta(minutes=2))
    newest = await _seed(factory, created_at=base - timedelta(minutes=1))

    resp = await authed_client.get("/notifications", params={"pageSize": 2})
    page1 = resp.json()["data"]
    assert [n["id"] for n in page1["notifications"]] == [newest, middle]
    assert page1["page"]["hasMore"] is True
    assert page1["page"]["total"] == 3
    cursor = page1["page"]["nextStart"]
    assert cursor

    resp = await authed_client.get(
        "/notifications", params={"pageSize": 2, "pageStart": cursor}
    )
    page2 = resp.json()["data"]
    assert [n["id"] for n in page2["notifications"]] == [oldest]
    assert page2["page"]["hasMore"] is False


@pytest.mark.anyio
async def test_read_filter_and_type_filter(authed_client: AsyncClient) -> None:
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    await _seed(factory, read=False, type_=NotificationType.MENTION)
    await _seed(factory, read=True, type_=NotificationType.REPLY)

    # read=false filter → only the unread mention.
    resp = await authed_client.get(
        "/notifications", params={"pageSize": 10, "read": False}
    )
    data = resp.json()["data"]
    assert data["page"]["total"] == 1
    assert data["notifications"][0]["type"] == "MENTION"

    # type filter narrows to REPLY.
    resp = await authed_client.get(
        "/notifications", params={"pageSize": 10, "type": "REPLY"}
    )
    data = resp.json()["data"]
    assert data["page"]["total"] == 1
    assert data["notifications"][0]["type"] == "REPLY"

    # Unknown type → 400.
    resp = await authed_client.get(
        "/notifications", params={"pageSize": 10, "type": "NOPE"}
    )
    assert resp.status_code == 400


async def _seed_project(factory) -> str:
    """一个项目，直接落行 —— ``POST /projects`` 在这套 harness 里是 503（它要的代码
    托管服务没配），而这个用例要的只是一个收得下通知的项目。"""
    from app.domain.project.models import Project

    async with factory() as session:
        project = Project(
            team_id=await a_team(session), name="并表", owner_handle=_AGENT_HANDLE
        )
        session.add(project)
        await session.flush()
        project_id = str(project.id)
        await session.commit()
    return project_id


async def _seed_user_with_profile(factory, nickname: str) -> int:
    """Seed a User + UserProfile and return the user id, so a notification's
    metadata can reference a resolvable actor."""
    from app.domain.user.models import User, UserProfile

    now = datetime.now(UTC)
    async with factory() as session:
        user = User(
            username=f"actor-{nickname}",
            email=f"actor-{nickname}@example.com",
            hashed_password="x",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(
            UserProfile(
                user_id=user.id,
                nickname=nickname,
                intro="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
        )
        user_id = user.id
        await session.commit()
    return user_id


@pytest.mark.anyio
async def test_entities_resolved_from_metadata(authed_client: AsyncClient) -> None:
    """A notification whose metadata references a user resolves to that user's
    display info through the wired resolvers."""
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    actor_id = await _seed_user_with_profile(factory, "Mochi")
    await _seed(
        factory,
        metadata={"actor": {"type": "user", "id": str(actor_id)}},
    )

    resp = await authed_client.get("/notifications", params={"pageSize": 10})
    entities = resp.json()["data"]["notifications"][0]["entities"]
    assert "actor" in entities
    assert entities["actor"] is not None
    assert entities["actor"]["type"] == "user"
    assert entities["actor"]["id"] == str(actor_id)
    assert entities["actor"]["name"] == "Mochi"


@pytest.mark.anyio
async def test_an_unfinalized_row_never_lights_the_unread_count(
    authed_client: AsyncClient,
) -> None:
    """小点上的数就是待办页列出来的行数 —— 数（`/notifications/unread-count`）和
    名单（`/notifications`）过同一套判据。

    还没落定的那一行不在列表里，所以也不能进未读数：那会让小点亮着、点进去一条未读
    也读不到，而唯一能按灭它的「全部已读」按钮只在列表里真有未读行时才画出来 ——
    那颗点谁都清不掉。
    """
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    landed = await _seed(factory)
    await _seed(factory, finalized=False)

    listed = await authed_client.get(
        "/notifications", params={"pageSize": 10, "read": False}
    )
    data = listed.json()["data"]
    assert [n["id"] for n in data["notifications"]] == [landed]

    unread = await authed_client.get("/notifications/unread-count")
    assert unread.json()["data"]["count"] == len(data["notifications"])


async def _seed_page_with_project_entities(factory, count: int) -> list[int]:
    """``count`` 条站内信，每条指向一个**不同的**项目。

    项目是逐条解析时最贵的那一种实体（每条一次 ``get_or_404``），而且指向不同的
    项目才不会让 SQLAlchemy 的身份映射替我们把重复的查询吃掉 —— 那样量出来的就
    不是端点的开销了。
    """
    from app.domain.project.models import Project

    now = datetime.now(UTC)
    ids: list[int] = []
    async with factory() as session:
        team_id = await a_team(session)
        for _ in range(count):
            project = Project(team_id=team_id, name="P", owner_handle=_AGENT_HANDLE)
            session.add(project)
            await session.flush()
            row = Notification(
                receiver_id=_AGENT_USER_ID,
                type=NotificationType.MENTION,
                read=False,
                finalized=True,
                metadata_payload={
                    "project": {"type": "project", "id": str(project.id)}
                },
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
        await session.commit()
    return ids


@pytest.mark.anyio
async def test_a_page_of_notifications_costs_constant_queries(
    authed_client: AsyncClient,
) -> None:
    """整页实体解析的查询数是常数，不随页里的条数长。

    页里每条都引一个项目实体，而项目解析曾经是逐 id ``get_or_404`` —— 一屏 20 条
    就是 20 次往返。这里在同一个测试里量两个规模（1 条对 20 条），比的不是一个固定
    数字，而是**这个数字不动**：固定数字只会钉住今天这个端点，两个规模互比才钉住
    真正要紧的那件事。
    """
    import re

    from sqlalchemy import event

    factory = authed_client.test_factory  # type: ignore[attr-defined]
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    engine = authed_client.test_app_engine.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    try:
        await _seed_page_with_project_entities(factory, 1)
        statements.clear()
        resp = await authed_client.get("/notifications", params={"pageSize": 20})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]["notifications"]) == 1
        one = list(statements)

        await _seed_page_with_project_entities(factory, 19)
        statements.clear()
        resp = await authed_client.get("/notifications", params={"pageSize": 20})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]["notifications"]) == 20
        twenty = list(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    # ``\b`` so ``FROM project_members`` / ``FROM project_forges`` do not count.
    def project_reads(log: list[str]) -> int:
        return sum(1 for s in log if re.search(r"\bFROM projects\b", s))

    # 整页一次查完：1 条和 20 条读项目表都是一次。
    assert project_reads(one) == 1
    assert project_reads(twenty) == 1
    # 端点的总往返数也不随页里的条数长。
    assert len(twenty) == len(one)
