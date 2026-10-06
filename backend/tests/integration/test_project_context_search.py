"""From one room, find what the rest of the project already says — only in the
rooms the caller may read, and never in another project. Every word asked for
has to be found, part of a word counts, and the best match comes first."""

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.living_doc.models import DocumentNode
from app.domain.living_doc.services import Documents
from app.domain.project.models import ProjectArtifact
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.repositories import TopicRepository
from tests.integration.conftest import (
    post_project,
    registered,
    session_auth_headers,
)
from tests.support.living_doc import document_of

OWNER = "user-1"


def _project(client) -> str:
    r = post_project(
        client, json={"name": f"上下文-{uuid.uuid4().hex[:6]}"}, owner=OWNER
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seed(client, fn):
    async def go():
        async with client.test_factory() as db:
            await fn(db)
            await db.commit()

    client.portal.call(go)


def _say(project: str, room: str, text: str, kind=BlockKind.message, author=OWNER):
    async def go(db):
        db.add(
            Block(
                project_id=uuid.UUID(project),
                conversation_id=uuid.UUID(room),
                kind=kind,
                author_type=AuthorType.participant,
                author=author,
                content=text,
            )
        )

    return go


def _paragraph(project: str, room: str, text: str):
    """A paragraph of the room's document."""

    async def go(db):
        doc = await Documents(db).ensure_for_room(
            room_id=uuid.UUID(room), project_id=uuid.UUID(project)
        )
        doc.version = max(doc.version, 1)
        db.add(
            DocumentNode(
                document_id=doc.id,
                node_type="paragraph",
                content=text,
                position=0,
                author=OWNER,
            )
        )

    return go


def _search(client, project, here, q, headers=None):
    r = client.get(
        f"/projects/{project}/context/search",
        params={"q": q, "topic": here} if here else {"q": q},
        headers=headers or {},
    )
    return r


def test_a_room_finds_what_another_room_of_the_project_said(client):
    project = _project(client)
    here = _room(client, project, "周报")
    there = _room(client, project, "预算讨论")
    _seed(client, _say(project, there, "预算定为 48 万元，其中外包 12 万"))
    _seed(client, _say(project, there, "预算按 48 万执行", kind=BlockKind.weekly))

    async def task(db):
        db.add(
            Task(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project),
                room_id=uuid.UUID(there),
                title="核对预算执行",
                status=TaskStatus.closed,
                conclusion="预算执行到 31.5 万",
            )
        )

    _seed(client, task)

    r = _search(client, project, here, "预算")
    assert r.status_code == 200, r.text
    hits = r.json()["data"]["hits"]
    kinds = {h["kind"] for h in hits["records"]}
    assert {"message", "weekly"} <= kinds
    assert all(h["room_id"] == there for h in hits["records"])
    assert hits["records"][0]["room_title"] == "预算讨论"
    assert [t["title"] for t in hits["tasks"]] == ["核对预算执行"]
    assert any(r["room_id"] == there for r in hits["rooms"])


def test_a_private_room_and_another_project_stay_out(client):
    project = _project(client)
    here = _room(client, project, "周报")
    other = _project(client)
    elsewhere = _room(client, other, "别人的项目")
    _seed(client, _say(other, elsewhere, "机密预算 99 万"))

    async def private_room(db):
        room = await TopicRepository(db).add(
            project_id=uuid.UUID(project), title="私人预算房间"
        )
        room.is_private = True
        await db.flush()
        db.add(
            Block(
                project_id=uuid.UUID(project),
                conversation_id=room.id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author=OWNER,
                content="私下说的预算 77 万",
            )
        )
        db.add(
            Task(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project),
                room_id=room.id,
                title="私下的预算任务",
                status=TaskStatus.open,
            )
        )

    _seed(client, private_room)

    data = _search(client, project, here, "预算").json()["data"]
    snippets = " ".join(h["snippet"] for h in data["hits"]["records"])
    assert "99 万" not in snippets, "another project's room was searched"
    assert "77 万" not in snippets, "a private room the caller is not in was searched"
    assert "私下的预算任务" not in [t["title"] for t in data["hits"]["tasks"]]
    assert "私人预算房间" not in [r["room_title"] for r in data["hits"]["rooms"]]
    assert data["skipped_rooms"] >= 1


def test_a_person_outside_the_project_is_refused(client):
    project = _project(client)
    _room(client, project, "周报")
    r = _search(client, project, None, "预算", headers=session_auth_headers("user-2"))
    assert r.status_code in (401, 403)


def test_nothing_found_is_an_answer_not_an_error(client):
    project = _project(client)
    here = _room(client, project, "周报")
    data = _search(client, project, here, "根本不存在的词").json()["data"]
    assert data["total"] == 0
    assert data["searched_rooms"] >= 1


def _task(project: str, room: str, *, key: str, title: str, conclusion: str, at):
    async def go(db):
        db.add(
            Task(
                id=uuid.UUID(key),
                project_id=uuid.UUID(project),
                room_id=uuid.UUID(room),
                title=title,
                status=TaskStatus.closed,
                conclusion=conclusion,
                created_at=at,
            )
        )

    return go


def test_a_task_named_after_the_words_comes_before_one_that_mentions_them(client):
    project = _project(client)
    here = _room(client, project, "周报")
    now = datetime.now(UTC)
    # Older, and later in id order: neither time nor id puts it first.
    _seed(
        client,
        _task(
            project,
            here,
            key="ffffffff-0000-4000-8000-000000000000",
            title="供应商合同续签",
            conclusion="已签",
            at=now - timedelta(days=2),
        ),
    )
    _seed(
        client,
        _task(
            project,
            here,
            key="00000000-0000-4000-8000-000000000000",
            title="季度复盘",
            conclusion="顺带提到供应商合同的付款节点",
            at=now,
        ),
    )

    tasks = _search(client, project, here, "供应商合同").json()["data"]["hits"]["tasks"]
    assert [t["title"] for t in tasks] == ["供应商合同续签", "季度复盘"]


def test_part_of_a_word_and_every_word_are_found(client):
    project = _project(client)
    here = _room(client, project, "周报")
    _seed(client, _say(project, here, "季度财务报表整理好了，导出给客户"))
    _seed(client, _say(project, here, "财务报表还差审计"))

    def found(q):
        data = _search(client, project, here, q).json()["data"]
        return sorted(h["snippet"] for h in data["hits"]["records"])

    # 「务报」 straddles two words of 「财务报表」.
    assert found("务报") == ["季度财务报表整理好了，导出给客户", "财务报表还差审计"]
    assert found("财务报表 导出") == ["季度财务报表整理好了，导出给客户"]
    assert found("财务报表 天气") == []


def test_artifacts_rank_by_name_and_stay_in_their_project(client):
    project = _project(client)
    here = _room(client, project, "周报")
    other = _project(client)

    async def artifacts(db):
        db.add(
            ProjectArtifact(project_id=uuid.UUID(project), name="年度报告", about="")
        )
        db.add(
            ProjectArtifact(
                project_id=uuid.UUID(project), name="附件汇编", about="年度报告的附录"
            )
        )
        db.add(ProjectArtifact(project_id=uuid.UUID(other), name="年度报告", about=""))

    _seed(client, artifacts)

    found = _search(client, project, here, "年度报告").json()["data"]["hits"]
    assert [a["name"] for a in found["artifacts"]] == ["年度报告", "附件汇编"]


def test_a_busy_conversation_does_not_crowd_out_the_documents(client):
    """``limit`` is per kind: a hundred matching messages still leave room for
    the one weekly report and the one document paragraph that match too."""
    project = _project(client)
    room = _room(client, project, "排期")
    # The messages are short and say little else, so each outranks the longer
    # weekly report and document paragraph.
    for i in range(6):
        _seed(client, _say(project, room, f"上线日期？{i}"))
    long = "经过三轮讨论，考虑到测试、审批和宣传各自需要的时间，"
    _seed(
        client,
        _say(project, room, long + "上线日期定在 10 月 8 日", kind=BlockKind.weekly),
    )
    _seed(
        client,
        _paragraph(project, room, long + "上线日期以周报为准"),
    )

    r = client.get(
        f"/projects/{project}/context/search",
        params={"q": "上线日期", "topic": room, "limit": 3},
    )
    assert r.status_code == 200, r.text
    records = r.json()["data"]["hits"]["records"]
    kinds = [h["kind"] for h in records]
    assert kinds.count("message") == 3
    assert "weekly" in kinds
    assert "doc_node" in kinds


# 搜索结果页：一类一类地看，一页一页地翻，先知道每类有多少。翻完所有页，拿到的
# 正好是这一类的全部、不重不漏；条数和翻出来的一样多；只问的那几类之外什么都不给。


def test_paging_through_one_kind_gives_every_hit_once(client):
    project = _project(client)
    room = _room(client, project, "排期")
    for i in range(7):
        _seed(client, _say(project, room, f"发布窗口第 {i} 次讨论"))
    _seed(client, _say(project, room, "发布窗口定了", kind=BlockKind.weekly))

    seen: list[str] = []
    for offset in (0, 3, 6):
        r = client.get(
            f"/projects/{project}/context/search",
            params={
                "q": "发布窗口",
                "topic": room,
                "only": "message",
                "limit": 3,
                "offset": offset,
            },
        )
        assert r.status_code == 200, r.text
        hits = r.json()["data"]["hits"]
        assert {h["kind"] for h in hits["records"]} <= {"message"}
        assert hits["tasks"] == []
        seen += [h["id"] for h in hits["records"]]

    assert len(seen) == 7
    assert len(set(seen)) == 7


def test_one_page_can_hold_several_kinds(client):
    project = _project(client)
    room = _room(client, project, "文档")
    _seed(client, _paragraph(project, room, "接口约定写在这里"))
    owner = session_auth_headers(OWNER)
    commented = client.post(
        f"/documents/{document_of(client, room, headers=owner)}/comments",
        json={"content": "接口约定第二段要改", "quote": "接口约定"},
        headers=owner,
    )
    assert commented.status_code == 200, commented.text
    _seed(client, _say(project, room, "接口约定聊过了"))

    r = client.get(
        f"/projects/{project}/context/search",
        params=[
            ("q", "接口约定"),
            ("topic", room),
            ("only", "doc_node"),
            ("only", "comment"),
        ],
    )
    assert r.status_code == 200, r.text
    kinds = sorted(h["kind"] for h in r.json()["data"]["hits"]["records"])
    assert kinds == ["comment", "doc_node"]


def test_counts_match_what_the_pages_hold(client):
    project = _project(client)
    room = _room(client, project, "预算")
    for i in range(4):
        _seed(client, _say(project, room, f"季度预算 {i}"))
    _seed(client, _say(project, room, "季度预算按此执行", kind=BlockKind.weekly))

    r = client.get(
        f"/projects/{project}/context/search",
        params={"q": "季度预算", "topic": room, "with_counts": True},
    )
    assert r.status_code == 200, r.text
    counts = r.json()["data"]["counts"]
    assert counts["message"] == 4
    assert counts["weekly"] == 1
    assert counts["tasks"] == 0


def test_counts_leave_out_rooms_the_caller_cannot_read(client):
    project = _project(client)
    here = _room(client, project, "公开")

    async def private_room(db):
        room = await TopicRepository(db).add(
            project_id=uuid.UUID(project), title="私人房间"
        )
        room.is_private = True
        await db.flush()
        db.add(
            Block(
                project_id=uuid.UUID(project),
                conversation_id=room.id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author=OWNER,
                content="私下的年终奖",
            )
        )

    _seed(client, private_room)
    _seed(client, _say(project, here, "公开的年终奖"))

    counts = client.get(
        f"/projects/{project}/context/search",
        params={"q": "年终奖", "topic": here, "with_counts": True},
    ).json()["data"]["counts"]
    assert counts["message"] == 1


def test_an_unknown_kind_is_refused(client):
    project = _project(client)
    room = _room(client, project, "随便")
    r = client.get(
        f"/projects/{project}/context/search",
        params={"q": "什么", "topic": room, "only": "passwords"},
    )
    assert r.status_code == 422


# 搜索前先要知道「这个人能看哪些房间」。房间多了，这一步不能跟着一间一间多问：
# 5 个房间和 30 个房间，一次搜索发出的查询一样多。


def _statements(client):
    from sqlalchemy import event

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    engine = client.test_request_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    return statements, lambda: event.remove(engine, "before_cursor_execute", record)


def _cost_of_one_search(client, rooms: int) -> int:
    project = _project(client)
    here = _room(client, project, "起点")
    for i in range(rooms - 1):
        _room(client, project, f"房间 {i}")
    statements, stop = _statements(client)
    try:
        r = client.get(
            f"/projects/{project}/context/search",
            params={"q": "预算", "topic": here, "with_counts": True},
            headers=session_auth_headers(OWNER),
        )
    finally:
        stop()
    assert r.status_code == 200, r.text
    # 建项目时自带一个「全局」房间。
    assert r.json()["data"]["searched_rooms"] == rooms + 1
    return len(statements)


def test_checking_which_rooms_to_search_does_not_grow_with_the_rooms(client):
    assert _cost_of_one_search(client, 30) == _cost_of_one_search(client, 5)


# A hit names who wrote it the way the rest of the product does: by the name
# they go by when the search is made, not by the handle stored on the record.


def _nickname(handle: str, nickname: str):
    """``handle`` goes by ``nickname`` from now on."""

    async def go(db):
        from sqlalchemy import select

        from app.domain.user.models import UserProfile

        user_id = await registered(db, handle)
        profile = await db.scalar(
            select(UserProfile).where(
                UserProfile.user_id == user_id, UserProfile.deleted_at.is_(None)
            )
        )
        if profile is None:
            now = datetime.now(UTC)
            db.add(
                UserProfile(
                    user_id=user_id,
                    nickname=nickname,
                    intro="",
                    avatar_id=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            profile.nickname = nickname

    return go


def _authors(client, project, q, **params) -> dict[str, tuple]:
    r = client.get(
        f"/projects/{project}/context/search",
        params={"q": q, **params},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    return {
        h["snippet"]: (h["author"], h["author_name"], h["author_name_source"])
        for h in r.json()["data"]["hits"]["records"]
    }


def test_a_hit_names_a_person_by_the_nickname_they_have_now(client):
    project = _project(client)
    room = _room(client, project, "限流")
    _seed(client, _nickname(OWNER, "Ada"))
    _seed(client, _say(project, room, "限流阈值定为每秒五十"))
    _seed(client, _paragraph(project, room, "限流阈值写进文档"))
    _seed(client, _say(project, room, "限流先观察一周", author="no-such-person"))

    everything = _authors(client, project, "限流阈值")
    assert everything == {
        "限流阈值定为每秒五十": (OWNER, "Ada", None),
        "限流阈值写进文档": (OWNER, "Ada", None),
    }

    _seed(client, _nickname(OWNER, "Ada Lovelace"))
    one_kind = _authors(client, project, "限流", only=["message"])
    assert one_kind == {
        "限流阈值定为每秒五十": (OWNER, "Ada Lovelace", None),
        # No one on the platform has that handle: no name to show.
        "限流先观察一周": ("no-such-person", None, None),
    }


def test_a_hit_names_a_teammate_by_its_name_in_the_project_now(client):
    project = _project(client)
    room = _room(client, project, "压测")
    made = client.post(
        f"/projects/{project}/agents",
        json={"handle": "cheese-kimi", "display_name": "Kimi"},
    )
    assert made.status_code == 200, made.text
    kimi = made.json()["data"]
    # A teammate writes under its seat; its own handle names it as well.
    _seed(client, _say(project, room, "压测结果已出", author=kimi["seat_handle"]))
    _seed(client, _say(project, room, "压测还要再跑", author="cheese-kimi"))

    assert _authors(client, project, "压测") == {
        "压测结果已出": (kimi["seat_handle"], "Kimi", "human"),
        "压测还要再跑": ("cheese-kimi", "Kimi", "human"),
    }

    renamed = client.put(
        f"/projects/{project}/agents/{kimi['id']}", json={"display_name": "Kimi K3"}
    )
    assert renamed.status_code == 200, renamed.text
    names = {
        name
        for _, name, _ in _authors(client, project, "压测", only=["message"]).values()
    }
    assert names == {"Kimi K3"}
