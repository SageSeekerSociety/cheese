"""「我的学习洞察」的两条路由：读自己的结果，标记自己的一条判断不准。

按浏览器会收到的状态码与 JSON 来断。判断行直接写进库（每晚分析怎么写下它们在
``test_space_learning_nightly.py`` 里测），这里只问页面读出来的是什么：

- 只有本人：同一块板上另一个成员的判断、依据，一样都读不到；板外的人整条 404；
- 全部知识点按清单顺序列出，没有判断的是「未涉及」（``judgment`` 为空）；
- 依据读回来是这个人自己的那条消息的摘录，带着它在哪道题；
- 最该补的：薄弱先于不稳；被标「不准」的不参选；
- 标记不准只能标自己的：别人的那条答 404，和不存在一个样子；
- 板没开学情分析：读到的是明确的「未开启」（``enabled: false``），标记答 409。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.project.models import Project
from app.domain.space.models import (
    SpaceLearningCursor,
    SpaceLearningJudgment,
    SpaceLearningQuestion,
)
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    project_service,
    unique_int,
)

SAID_AT = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Insight Board ({unique_int()})",
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
    for name in ("相关子查询", "GROUP BY 与 HAVING", "索引"):
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
            "knowledgePointIds": [],
        },
        headers=_auth(token),
    )
    assert task.status_code in (200, 201), task.text
    return {
        "token": token,
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


def _seed(_portal, db_session, board: dict, user, judgments: list[tuple]) -> dict:
    """``user`` 在这道题里说的一条话，以及几条指向它的判断（知识点下标, 档位, 不准?）。"""

    async def write() -> dict:
        project = await project_service(db_session).create(
            name="P", owner_handle=user.username
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
                content=f"{user.username} 问：子查询里怎么引用外层那一行？",
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
        )
        await db_session.flush()
        ids = []
        for offset, (index, level, disputed) in enumerate(judgments):
            judgment = SpaceLearningJudgment(
                space_id=board["space_id"],
                user_id=user.user_id,
                knowledge_point_id=board["points"][index],
                level=level,
                summary=f"{user.username} 的判断 {index}",
                evidence_block_ids=[block_id],
                disputed_at=SAID_AT if disputed else None,
                analyzed_at=SAID_AT + timedelta(minutes=offset),
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
            db_session.add(judgment)
            await db_session.flush()
            ids.append(judgment.id)
        db_session.add(
            SpaceLearningCursor(
                space_id=board["space_id"],
                user_id=user.user_id,
                task_id=board["task_id"],
                last_block_at=SAID_AT,
                last_block_id=block_id,
                messages_read=7,
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
        )
        for _ in range(2):
            db_session.add(
                SpaceLearningQuestion(
                    space_id=board["space_id"],
                    user_id=user.user_id,
                    task_id=board["task_id"],
                    block_id=uuid.uuid4(),
                    knowledge_point_id=board["points"][0],
                    kind="concept",
                    gist="怎么引用外层的列",
                    asked_at=SAID_AT,
                    created_at=SAID_AT,
                )
            )
        await db_session.flush()
        return {"block_id": str(block_id), "judgments": ids}

    return _portal.call(write)


def _mine(api_client: TestClient, token: str, space_id: int) -> dict:
    resp = api_client.get(
        f"/spaces/{space_id}/learning-analysis/me", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _dispute(api_client: TestClient, token: str, space_id: int, judgment_id: int):
    return api_client.post(
        f"/spaces/{space_id}/learning-analysis/me/judgments/{judgment_id}/dispute",
        headers=_auth(token),
    )


class TestMyInsight:
    def test_a_member_reads_their_own_judgments_and_nobody_elses(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        bob = _member(user_client, api_client, board)
        mine = _seed(
            _portal, db_session, board, ann, [(0, "weak", False), (1, "shaky", False)]
        )
        _seed(_portal, db_session, board, bob, [(2, "mastered", False)])

        data = _mine(api_client, ann.token, board["space_id"])
        assert data["enabled"] is True
        # 全部知识点按清单顺序；没有判断的是「未涉及」。
        assert [p["name"] for p in data["knowledgePoints"]] == [
            "相关子查询",
            "GROUP BY 与 HAVING",
            "索引",
        ]
        levels = [(p["judgment"] or {}).get("level") for p in data["knowledgePoints"]]
        assert levels == ["weak", "shaky", None]  # bob 的「索引」不在 ann 这里
        assert bob.username not in str(data)

        first = data["knowledgePoints"][0]["judgment"]
        (evidence,) = first["evidence"]
        assert evidence["blockId"] == mine["block_id"]
        assert evidence["taskName"] == "第 3 题"
        assert ann.username in evidence["quote"]

        # 最该补的：薄弱先于不稳。
        assert data["focusKnowledgePointId"] == board["points"][0]
        assert data["sources"]["messagesRead"] == 7
        assert [t["name"] for t in data["sources"]["tasks"]] == ["第 3 题"]
        (frequent,) = data["frequentQuestions"]
        assert frequent["count"] == 2 and frequent["kind"] == "concept"
        assert data["updatedAt"] is not None

    def test_a_member_with_nothing_analysed_yet_reads_an_empty_page(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        data = _mine(api_client, ann.token, board["space_id"])
        assert data["enabled"] is True
        assert data["updatedAt"] is None
        assert data["focusKnowledgePointId"] is None
        assert all(p["judgment"] is None for p in data["knowledgePoints"])

    def test_someone_outside_the_space_cannot_read_it(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        stranger = user_client.create_user()
        token = user_client.login(api_client, stranger.username, stranger.password)
        resp = api_client.get(
            f"/spaces/{board['space_id']}/learning-analysis/me", headers=_auth(token)
        )
        assert resp.status_code == 404, resp.text

    def test_a_space_without_the_analysis_says_it_is_off(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        seeded = _seed(_portal, db_session, board, ann, [(0, "weak", False)])
        off = api_client.patch(
            f"/spaces/{board['space_id']}/learning-analysis",
            json={"enabled": False},
            headers=_auth(board["token"]),
        )
        assert off.status_code == 200, off.text

        data = _mine(api_client, ann.token, board["space_id"])
        assert data["enabled"] is False
        assert data["knowledgePoints"] == []
        resp = _dispute(
            api_client, ann.token, board["space_id"], seeded["judgments"][0]
        )
        assert resp.status_code == 409, resp.text


class TestMarkingAJudgmentInaccurate:
    def test_a_disputed_judgment_is_marked_and_no_longer_the_focus(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        seeded = _seed(
            _portal, db_session, board, ann, [(0, "weak", False), (1, "shaky", False)]
        )
        weak_id = seeded["judgments"][0]

        resp = _dispute(api_client, ann.token, board["space_id"], weak_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {"judgmentId": weak_id, "disputed": True}

        data = _mine(api_client, ann.token, board["space_id"])
        first = data["knowledgePoints"][0]["judgment"]
        assert first["disputed"] is True
        # 降权：标过不准的不再是「最该补的」，轮到不稳的那一条。
        assert data["focusKnowledgePointId"] == board["points"][1]

        # 再标一次不出错。
        again = _dispute(api_client, ann.token, board["space_id"], weak_id)
        assert again.status_code == 200, again.text

    def test_nobody_can_mark_another_members_judgment(
        self, user_client: UserCreator, api_client: TestClient, _portal, db_session
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        bob = _member(user_client, api_client, board)
        bobs = _seed(_portal, db_session, board, bob, [(0, "weak", False)])

        resp = _dispute(api_client, ann.token, board["space_id"], bobs["judgments"][0])
        assert resp.status_code == 404, resp.text
        data = _mine(api_client, bob.token, board["space_id"])
        assert data["knowledgePoints"][0]["judgment"]["disputed"] is False
