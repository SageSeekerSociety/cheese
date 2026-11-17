"""「数据 › 学情分析」：老师看全班整体的几条路由与导出。

按浏览器会收到的状态码、JSON 和文件来断。判断、提问、误解、问题簇这些行直接写进库
（每晚分析怎么写下它们在 ``test_space_learning_nightly.py`` 里测），这里只问老师读出来的
是什么。规则（先写下来、再写的代码）：

- 门是「数据」页那一道：管理员读得到，普通成员、板外的人读不到；
- 任何一组不足 3 人不分析：只报 ``analysed: false``，人数与内容一概不给；
- 例句、摘录只标本空间固定代号（同一个人在同一块板上不变），返回里没有成员的 handle
  或名字；原话里的学号、手机号遮掉；
- 「查看原文」的坐标只给读得了那个项目的人；
- 板没开学情分析：明确的 ``enabled: false``；
- 导出的文件只有整体：不含成员的 handle；导出记下谁、什么格式。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pymupdf
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.project.models import Project
from app.domain.space.learning_privacy import member_code
from app.domain.space.models import (
    SpaceLearningCluster,
    SpaceLearningExport,
    SpaceLearningJudgment,
    SpaceLearningMisconception,
    SpaceLearningQuestion,
)
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    project_service,
    unique_int,
)

SAID_AT = datetime.now(UTC) - timedelta(days=1)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Class Board ({unique_int()})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    space_id = space["id"]
    on = api_client.patch(
        f"/spaces/{space_id}/learning-analysis",
        json={"enabled": True},
        headers=_auth(token),
    )
    assert on.status_code == 200, on.text
    points = []
    for name in ("相关子查询", "GROUP BY 与 HAVING"):
        added = api_client.post(
            f"/spaces/{space_id}/learning-analysis/knowledge-points",
            json={"name": name},
            headers=_auth(token),
        )
        assert added.status_code == 201, added.text
        points.append(added.json()["data"]["knowledgePoint"]["id"])
    task = api_client.post(
        "/tasks",
        json={
            "mentors": [],
            "name": "第 3 题",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": space_id,
            "categoryId": space.get("defaultCategoryId"),
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": 4_000_000_000_000,
            "knowledgePointIds": points,
        },
        headers=_auth(token),
    )
    assert task.status_code in (200, 201), task.text
    return {
        "token": token,
        "owner": creator,
        "space_id": space_id,
        "points": points,
        "task_id": task.json()["data"]["task"]["id"],
    }


def _member(user_client: UserCreator, api_client: TestClient, board: dict):
    user = user_client.create_user()
    added = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["token"]),
    )
    assert added.status_code == 201, added.text
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _rows(_portal, db_session, write):
    async def run():
        result = await write()
        await db_session.flush()
        return result

    return _portal.call(run)


def _misconception(_portal, db_session, board: dict, point: int, label: str) -> int:
    async def write():
        row = SpaceLearningMisconception(
            space_id=board["space_id"],
            knowledge_point_id=board["points"][point],
            label=label,
            created_at=SAID_AT,
        )
        db_session.add(row)
        await db_session.flush()
        return row.id

    return _rows(_portal, db_session, write)


def _cluster(_portal, db_session, board: dict, label: str) -> int:
    async def write():
        row = SpaceLearningCluster(
            space_id=board["space_id"],
            knowledge_point_id=board["points"][0],
            label=label,
            created_at=SAID_AT,
        )
        db_session.add(row)
        await db_session.flush()
        return row.id

    return _rows(_portal, db_session, write)


def _seed(
    _portal,
    db_session,
    board: dict,
    user,
    *,
    level: str = "weak",
    misconception: int | None = None,
    cluster: int | None = None,
    point: int = 0,
    content: str | None = None,
    at: datetime | None = None,
) -> str:
    """``user`` 在这道题里说的一条话（带着自己的 handle、学号和手机号），一条指向它的
    判断，以及一条归进 ``cluster`` 的提问。返回那条话的 block id。"""

    async def write():
        project = await project_service(db_session).create(
            name=f"{user.username} 的作业", owner_handle=user.username
        )
        row = await db_session.get(Project, project.id)
        assert row is not None
        row.external_task_id = board["task_id"]
        block_id = uuid.uuid4()
        db_session.add(
            Block(
                id=block_id,
                project_id=project.id,
                conversation_id=project.root_topic_id,
                author=user.username,
                author_type=AuthorType.participant,
                kind=BlockKind.message,
                content=content
                or (
                    f"我是{user.username}，学号 2021012345，电话 13812345678。"
                    "子查询里怎么引用外层那一行？"
                ),
                created_at=at or SAID_AT,
                updated_at=at or SAID_AT,
            )
        )
        await db_session.flush()
        db_session.add(
            SpaceLearningJudgment(
                space_id=board["space_id"],
                user_id=user.user_id,
                knowledge_point_id=board["points"][point],
                level=level,
                summary="你还不清楚内层怎样引用外层那一行。",
                evidence_block_ids=[block_id],
                misconception_id=misconception,
                analyzed_at=SAID_AT,
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
        )
        db_session.add(
            SpaceLearningQuestion(
                space_id=board["space_id"],
                user_id=user.user_id,
                task_id=board["task_id"],
                block_id=block_id,
                knowledge_point_id=board["points"][point],
                kind="concept",
                gist="怎么引用外层的列",
                cluster_id=cluster,
                asked_at=SAID_AT,
                created_at=SAID_AT,
            )
        )
        return str(block_id)

    return _rows(_portal, db_session, write)


def _get(api_client: TestClient, board: dict, path: str, token: str | None = None):
    resp = api_client.get(
        f"/spaces/{board['space_id']}/learning-analysis/class/{path}",
        headers=_auth(token or board["token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestFewerThanThreeAreNotAnalysed:
    def test_a_point_with_two_people_says_nothing_and_with_three_it_counts(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        bob = _member(user_client, api_client, board)
        _seed(_portal, db_session, board, ann)
        _seed(_portal, db_session, board, bob)

        points = _get(api_client, board, "points")
        first = points["points"][0]
        assert first["analysed"] is False
        assert first["stuck"] is None and first["involved"] is None
        detail = _get(api_client, board, f"points/{board['points'][0]}")
        assert detail["analysed"] is False
        assert detail["misconceptions"] == [] and detail["mastery"] is None
        assert _get(api_client, board, "overview")["focus"] == []

        cat = _member(user_client, api_client, board)
        _seed(_portal, db_session, board, cat, level="shaky")
        points = _get(api_client, board, "points")
        first = points["points"][0]
        assert first["analysed"] is True
        assert (first["stuck"], first["involved"], first["asked"]) == (3, 3, 3)
        assert points["members"] == 3
        (focus,) = _get(api_client, board, "overview")["focus"]
        assert focus["knowledgePointId"] == board["points"][0]
        assert focus["stuck"] == 3

    def test_a_misconception_or_a_cluster_held_by_two_is_not_shown(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        common = _misconception(_portal, db_session, board, 0, "内层不知道怎样引用外层")
        rare = _misconception(_portal, db_session, board, 0, "以为子查询一定比 JOIN 慢")
        big = _cluster(_portal, db_session, board, "怎么引用外层的列？")
        small = _cluster(_portal, db_session, board, "能不能不用子查询？")
        people = [_member(user_client, api_client, board) for _ in range(5)]
        for person in people[:3]:
            _seed(_portal, db_session, board, person, misconception=common, cluster=big)
        for person in people[3:]:
            _seed(_portal, db_session, board, person, misconception=rare, cluster=small)

        detail = _get(api_client, board, f"points/{board['points'][0]}")
        assert detail["analysed"] is True
        assert [(m["label"], m["people"]) for m in detail["misconceptions"]] == [
            ("内层不知道怎样引用外层", 3)
        ]
        assert detail["hiddenMisconceptions"] == 1
        assert "JOIN 慢" not in str(detail)

        questions = _get(api_client, board, "questions")
        assert [(c["id"], c["people"]) for c in questions["clusters"]] == [(big, 3)]
        assert questions["hiddenClusters"] == 1

        resp = api_client.get(
            f"/spaces/{board['space_id']}/learning-analysis/class/excerpts",
            params={"cluster": small},
            headers=_auth(board["token"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["analysed"] is False
        assert resp.json()["data"]["excerpts"] == []


class TestOnlyTheClassAsAWhole:
    def test_quotes_carry_a_fixed_code_and_no_name_or_student_number(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        wrong = _misconception(_portal, db_session, board, 0, "内层不知道怎样引用外层")
        cluster = _cluster(_portal, db_session, board, "怎么引用外层的列？")
        people = [_member(user_client, api_client, board) for _ in range(3)]
        for person in people:
            _seed(
                _portal, db_session, board, person, misconception=wrong, cluster=cluster
            )
        codes = {member_code(board["space_id"], p.user_id) for p in people}

        detail = _get(api_client, board, f"points/{board['points'][0]}")
        examples = detail["misconceptions"][0]["examples"]
        assert examples and {e["code"] for e in examples} <= codes
        for example in examples:
            assert "[学号]" in example["quote"]
            assert "13812345678" not in example["quote"]
        # 同一个人再读一次还是同一个代号。
        again = _get(api_client, board, f"points/{board['points'][0]}")
        assert again["misconceptions"][0]["examples"] == examples

        excerpts = _get(api_client, board, f"excerpts?cluster={cluster}")
        assert excerpts["analysed"] is True
        assert {e["code"] for e in excerpts["excerpts"]} == codes
        # 老师读不了成员的项目：没有「查看原文」的坐标。
        assert all(e["source"] is None for e in excerpts["excerpts"])

        everything = [
            _get(api_client, board, path)
            for path in ("overview", "points", "questions", "tasks", "behavior")
        ] + [detail, excerpts]
        flat = str(everything)
        for person in people:
            assert person.username not in flat
            assert "2021012345" not in flat

    def test_the_code_is_fixed_per_space_and_differs_between_spaces(self):
        assert member_code(7, 42) == member_code(7, 42)
        assert member_code(7, 42) != member_code(8, 42)
        assert len(member_code(7, 42)) == 4


class TestFrequentWords:
    def test_words_and_errors_said_by_three_people_are_listed_and_names_are_not(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        people = [_member(user_client, api_client, board) for _ in range(3)]
        now = datetime.now(UTC)
        said = ("相关子查询报错了", "我不懂相关子查询", "相关子查询怎么写")
        for person, words in zip(people, said, strict=True):
            _seed(
                _portal,
                db_session,
                board,
                person,
                content=(
                    f"我叫{person.username}。{words}：\n"
                    "ERROR 1111 (HY000): Invalid use of group function"
                ),
                at=now,
            )
        # 只有一个人说过的词不列。
        _seed(
            _portal,
            db_session,
            board,
            people[0],
            content="笛卡尔积笛卡尔积笛卡尔积",
            at=now,
            point=1,
        )

        data = _get(api_client, board, "words")
        words = {w["word"]: w for w in data["words"]}
        assert "相关子查询" in words
        assert words["相关子查询"]["people"] == 3
        assert "笛卡尔积" not in words
        # 片段不是词：「子查询」前面永远是「关」。
        assert "子查询" not in words and "关子查询" not in words
        (error,) = data["errors"]
        assert error["line"].startswith("ERROR 1111")
        assert error["people"] == 3
        for person in people:
            assert person.username not in str(data)


class TestWhoReadsIt:
    def test_a_member_cannot_read_the_class_view_and_a_stranger_cannot_see_it(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        resp = api_client.get(
            f"/spaces/{board['space_id']}/learning-analysis/class/overview",
            headers=_auth(ann.token),
        )
        assert resp.status_code in (403, 404), resp.text
        stranger = user_client.create_user()
        token = user_client.login(api_client, stranger.username, stranger.password)
        resp = api_client.get(
            f"/spaces/{board['space_id']}/learning-analysis/class/overview",
            headers=_auth(token),
        )
        assert resp.status_code == 404, resp.text

    def test_a_space_without_the_analysis_says_it_is_off(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        for _ in range(3):
            _seed(_portal, db_session, board, _member(user_client, api_client, board))
        off = api_client.patch(
            f"/spaces/{board['space_id']}/learning-analysis",
            json={"enabled": False},
            headers=_auth(board["token"]),
        )
        assert off.status_code == 200, off.text
        overview = _get(api_client, board, "overview")
        assert overview["enabled"] is False and overview["focus"] == []
        assert _get(api_client, board, "points")["points"] == []


class TestExport:
    def _setup(self, user_client, api_client, _portal, db_session):
        board = _board(user_client, api_client)
        wrong = _misconception(_portal, db_session, board, 0, "内层不知道怎样引用外层")
        people = [_member(user_client, api_client, board) for _ in range(3)]
        for person in people:
            _seed(_portal, db_session, board, person, misconception=wrong)
        return board, people

    def _export(self, api_client, board, fmt: str, sections: list[str]):
        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/class/export",
            json={"format": fmt, "sections": sections},
            headers=_auth(board["token"]),
        )
        assert resp.status_code == 200, resp.text
        return resp.content

    def test_each_format_holds_only_the_class_and_the_export_is_recorded(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board, people = self._setup(user_client, api_client, _portal, db_session)
        sections = ["tasks", "points", "advice", "excerpts"]

        book = load_workbook(
            io.BytesIO(self._export(api_client, board, "xlsx", sections))
        )
        cells = " ".join(
            str(c.value)
            for sheet in book.worksheets
            for row in sheet.iter_rows()
            for c in row
            if c.value is not None
        )
        pdf = pymupdf.open(
            stream=self._export(api_client, board, "pdf", sections), filetype="pdf"
        )
        pdf_text = "".join(page.get_text() for page in pdf)
        word = Document(io.BytesIO(self._export(api_client, board, "docx", sections)))
        word_text = " ".join(
            [p.text for p in word.paragraphs]
            + [c.text for t in word.tables for r in t.rows for c in r.cells]
        )
        for text in (cells, pdf_text, word_text):
            assert "内层不知道怎样引用外层" in text
            for person in people:
                assert person.username not in text
            assert "2021012345" not in text
        # 摘录只标代号。
        codes = {member_code(board["space_id"], p.user_id) for p in people}
        assert any(code in cells for code in codes)

        async def exported():
            rows = await db_session.scalars(
                select(SpaceLearningExport).where(
                    SpaceLearningExport.space_id == board["space_id"]
                )
            )
            return [(r.user_id, r.format, list(r.sections)) for r in rows.all()]

        recorded = _portal.call(exported)
        assert sorted(f for _u, f, _s in recorded) == ["docx", "pdf", "xlsx"]
        assert {u for u, _f, _s in recorded} == {board["owner"].user_id}

    def test_excerpts_are_left_out_unless_ticked(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board, people = self._setup(user_client, api_client, _portal, db_session)
        book = load_workbook(
            io.BytesIO(self._export(api_client, board, "xlsx", ["points"]))
        )
        cells = " ".join(
            str(c.value)
            for sheet in book.worksheets
            for row in sheet.iter_rows()
            for c in row
            if c.value is not None
        )
        codes = {member_code(board["space_id"], p.user_id) for p in people}
        assert not any(code in cells for code in codes)
        assert "子查询里怎么引用外层" not in cells

    def test_a_member_cannot_export(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/class/export",
            json={"format": "pdf", "sections": ["points"]},
            headers=_auth(ann.token),
        )
        assert resp.status_code in (403, 404), resp.text
