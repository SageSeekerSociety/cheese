"""学情分析第 4 步的路由：巩固练习、复习提醒、讲解，与老师的「推送巩固题」。

按浏览器会收到的状态码与 JSON 来断；判断行直接写进库（每晚分析怎么写下它们在
``test_space_learning_nightly.py`` 里测），网关是一个按排好的话作答的 MockTransport。
规则（先写下来、再写的代码）：

- 出 3 道题：答过的题才带正确答案和讲解；第一次选的定下来；
- 做完一组并进判断：全对升一档，错两道降一档；作答统计记在判断上；排下一次复习；
- 只有本人：别人的一组题读不到、答不了（404，与不存在一个样子），也不出现在别人的页上；
- 每人每天的上限：到了答 429，模型不再被问；
- 讲解：判断没变时读缓存，不再问模型；
- 推送只给卡住的人（不稳 / 薄弱、没标不准），老师只看到人数、看不到名单；卡住的不足
  3 人不推；普通成员推不了；
- 知识点详情的「做过巩固题的人里几人升档」：不足 3 人不报。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.project.models import Project
from app.domain.space import learning_practice_analysis
from app.domain.space.models import SpaceLearningJudgment
from app.domain.space.practice_models import SpaceLearningPractice
from app.domain.usage import pricing
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    project_service,
    unique_int,
)

SAID_AT = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)

QUESTIONS = [
    {
        "stem": f"第 {n} 题：内层子查询怎么引用外层那一行？",
        "options": ["用外层表的别名", "用 GROUP BY", "写进 HAVING", "不能引用"],
        "answer": 0,
        "explanation": f"讲解 {n}：内层可以直接写外层的别名。",
    }
    for n in (1, 2, 3)
]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def gateway(monkeypatch):
    """签 key、报价、按排好的话作答的网关；路由不带 transport，所以把
    ``httpx.AsyncClient`` 换成默认挂上它的子类（同 ``test_space_learning_analysis``）。"""
    seen: dict = {"asked": [], "answers": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-learning-analysis"})
        if request.url.path == "/model/info":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "model_name": "deepseek-flash",
                            "litellm_params": {
                                "model": "deepseek-flash",
                                "input_cost_per_token": 0.0000005,
                                "output_cost_per_token": 0.0000015,
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            seen["asked"].append(body["messages"][-1]["content"])
            answer = seen["answers"].pop(0)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": answer}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 40},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            if kwargs.get("transport") is None:
                kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    monkeypatch.setattr(settings, "learning_analysis_model", "deepseek-flash")
    monkeypatch.setattr(settings, "learning_analysis_daily_calls_per_space", 50)
    pricing.forget()
    return seen


def _questions() -> str:
    return json.dumps({"questions": QUESTIONS})


def _board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Practice Board ({unique_int()})",
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
    added = api_client.post(
        f"/spaces/{space_id}/learning-analysis/knowledge-points",
        json={"name": "相关子查询"},
        headers=_auth(token),
    )
    assert added.status_code == 201, added.text
    point = added.json()["data"]["knowledgePoint"]["id"]
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
            "knowledgePointIds": [point],
        },
        headers=_auth(token),
    )
    assert task.status_code in (200, 201), task.text
    return {
        "token": token,
        "space_id": space_id,
        "point": point,
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


def _judged(_portal, db_session, board: dict, user, level: str, *, disputed=False):
    """``user`` 在题里说的一句话，和一条指着它的判断。"""

    async def write() -> int:
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
                content="子查询里怎么引用外层那一行我一直没搞懂",
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
        )
        judgment = SpaceLearningJudgment(
            space_id=board["space_id"],
            user_id=user.user_id,
            knowledge_point_id=board["point"],
            level=level,
            summary="你还不清楚内层怎样引用外层那一行。",
            evidence_block_ids=[block_id],
            disputed_at=SAID_AT if disputed else None,
            analyzed_at=SAID_AT,
            created_at=SAID_AT,
            updated_at=SAID_AT,
        )
        db_session.add(judgment)
        await db_session.flush()
        return judgment.id

    return _portal.call(write)


def _start(api_client, board, user, **extra):
    return api_client.post(
        f"/spaces/{board['space_id']}/learning-analysis/me/practice",
        json={"knowledgePointId": board["point"], **extra},
        headers=_auth(user.token),
    )


def _answer(api_client, board, user, practice_id, question, choice):
    return api_client.post(
        f"/spaces/{board['space_id']}/learning-analysis/me/practice/"
        f"{practice_id}/answers",
        json={"question": question, "choice": choice},
        headers=_auth(user.token),
    )


def _complete(api_client, board, user, practice_id):
    return api_client.post(
        f"/spaces/{board['space_id']}/learning-analysis/me/practice/"
        f"{practice_id}/complete",
        headers=_auth(user.token),
    )


def _mine(api_client, board, user, path="me/practice"):
    resp = api_client.get(
        f"/spaces/{board['space_id']}/learning-analysis/{path}",
        headers=_auth(user.token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestPractice:
    def test_a_member_practises_and_the_result_moves_their_judgment(
        self, user_client, api_client, _portal, db_session, gateway
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, ann, "weak")
        gateway["answers"].append(_questions())

        started = _start(api_client, board, ann)
        assert started.status_code == 200, started.text
        practice = started.json()["data"]
        assert practice["ready"] is True and len(practice["questions"]) == 3
        # 没答之前，答案和讲解都不在响应里。
        assert all(q["answer"] is None for q in practice["questions"])
        assert all(q["explanation"] is None for q in practice["questions"])
        # 模型读到的是他卡住的地方和他自己的话。
        assert "薄弱" in gateway["asked"][0]
        assert "子查询里怎么引用外层那一行我一直没搞懂" in gateway["asked"][0]

        first = _answer(api_client, board, ann, practice["id"], 0, 0).json()["data"]
        assert first["correct"] is True
        assert first["explanation"].startswith("讲解 1")
        # 第一次选的定下来：再答同一道，交回的还是第一次的。
        again = _answer(api_client, board, ann, practice["id"], 0, 2).json()["data"]
        assert again["chosen"] == 0 and again["correct"] is True
        # 没答完不能交。
        unfinished = _complete(api_client, board, ann, practice["id"])
        assert unfinished.status_code == 409, unfinished.text
        for question in (1, 2):
            _answer(api_client, board, ann, practice["id"], question, 0)

        result = _complete(api_client, board, ann, practice["id"])
        assert result.status_code == 200, result.text
        data = result.json()["data"]
        assert (data["correct"], data["total"]) == (3, 3)
        assert (data["levelBefore"], data["levelAfter"]) == ("weak", "shaky")
        assert data["nextReviewOn"] is not None

        insight = _mine(api_client, board, ann, "me")
        (point,) = insight["knowledgePoints"]
        assert point["judgment"]["level"] == "shaky"
        assert point["judgment"]["practice"]["correct"] == 3
        # 对话里的依据原样留着。
        assert point["judgment"]["evidence"]

        overview = _mine(api_client, board, ann)
        assert overview["open"] == []
        assert overview["upcoming"]["dueOn"] == data["nextReviewOn"]
        assert overview["usedToday"] == 1

    def test_two_wrong_answers_take_a_level_down(
        self, user_client, api_client, _portal, db_session, gateway
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, ann, "shaky")
        gateway["answers"].append(_questions())
        practice = _start(api_client, board, ann).json()["data"]
        for question, choice in ((0, 1), (1, 2), (2, 0)):
            _answer(api_client, board, ann, practice["id"], question, choice)

        data = _complete(api_client, board, ann, practice["id"]).json()["data"]
        assert (data["levelBefore"], data["levelAfter"]) == ("shaky", "weak")

    def test_nobody_else_can_read_or_answer_my_set(
        self, user_client, api_client, _portal, db_session, gateway
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        bob = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, ann, "weak")
        gateway["answers"].append(_questions())
        practice = _start(api_client, board, ann).json()["data"]

        url = (
            f"/spaces/{board['space_id']}/learning-analysis/me/practice/"
            f"{practice['id']}"
        )
        assert api_client.get(url, headers=_auth(bob.token)).status_code == 404
        assert _answer(api_client, board, bob, practice["id"], 0, 0).status_code == 404
        assert _complete(api_client, board, bob, practice["id"]).status_code == 404
        # 老师也一样：这一组只属于 ann。
        assert api_client.get(url, headers=_auth(board["token"])).status_code == 404
        assert _mine(api_client, board, bob)["open"] == []
        assert [row["id"] for row in _mine(api_client, board, ann)["open"]] == [
            practice["id"]
        ]

    def test_a_member_without_a_judgment_on_the_point_gets_no_questions(
        self, user_client, api_client, gateway
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        resp = _start(api_client, board, ann)
        assert resp.status_code == 404, resp.text
        assert gateway["asked"] == []

    def test_the_daily_cap_stops_asking_the_model(
        self, user_client, api_client, _portal, db_session, gateway, monkeypatch
    ):
        monkeypatch.setattr(learning_practice_analysis, "DAILY_CALLS_PER_MEMBER", 1)
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, ann, "weak")
        gateway["answers"].append(_questions())
        assert _start(api_client, board, ann).status_code == 200

        again = _start(api_client, board, ann, fresh=True)
        assert again.status_code == 429, again.text
        assert len(gateway["asked"]) == 1
        # 没出成的那一组不留着，原来那组也已经换掉了：没有半成品。
        assert _mine(api_client, board, ann)["open"] == []

    def test_a_failed_set_does_not_spend_the_member_s_daily_slot(
        self, user_client, api_client, _portal, db_session, gateway, monkeypatch
    ):
        """一次没出成东西的调用不扣学生当天的一格。

        模型答了一半（截断）解析不出来时，学生按了一次按钮、什么也没拿到；这一次不该
        占掉他的每日额度。上限设成 1：不退还的话，第二次就被自己的上限挡在门外。
        """
        monkeypatch.setattr(learning_practice_analysis, "DAILY_CALLS_PER_MEMBER", 1)
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, ann, "weak")
        # 第一问：JSON 被截在中间，读不出来。
        gateway["answers"].append('{"questions": [{"stem": "内层怎么引用外层那一行')
        broken = _start(api_client, board, ann)
        assert broken.status_code == 503, broken.text
        assert len(gateway["asked"]) == 1
        # 额度退还了：同一上限下还能再试，这次出满 3 道。
        gateway["answers"].append(_questions())
        again = _start(api_client, board, ann, fresh=True)
        assert again.status_code == 200, again.text
        assert len(again.json()["data"]["questions"]) == 3

    def test_the_explanation_is_read_from_its_cache_while_the_judgment_holds(
        self, user_client, api_client, _portal, db_session, gateway
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, ann, "weak")
        gateway["answers"].append(
            json.dumps({"explanation": "你可能以为内层看不见外层。其实……"})
        )
        url = f"/spaces/{board['space_id']}/learning-analysis/me/explanation"
        body = {"knowledgePointId": board["point"]}

        first = api_client.post(url, json=body, headers=_auth(ann.token))
        assert first.status_code == 200, first.text
        second = api_client.post(url, json=body, headers=_auth(ann.token))
        assert second.json()["data"]["text"] == first.json()["data"]["text"]
        assert len(gateway["asked"]) == 1


class TestPush:
    def test_the_push_reaches_only_the_stuck_and_the_teacher_sees_a_count(
        self, user_client, api_client, _portal, db_session, gateway
    ):
        board = _board(user_client, api_client)
        stuck = [_member(user_client, api_client, board) for _ in range(3)]
        for user, level in zip(stuck, ("weak", "shaky", "weak"), strict=True):
            _judged(_portal, db_session, board, user, level)
        fine = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, fine, "mastered")
        doubting = _member(user_client, api_client, board)
        _judged(_portal, db_session, board, doubting, "shaky", disputed=True)

        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/class/points/"
            f"{board['point']}/push",
            headers=_auth(board["token"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {"pushed": True, "recipients": 3, "minPeople": 3}
        for user in (*stuck, fine, doubting):
            assert user.username not in resp.text

        for user in stuck:
            (waiting,) = _mine(api_client, board, user)["open"]
            assert waiting["source"] == "pushed" and waiting["ready"] is False
        assert _mine(api_client, board, fine)["open"] == []
        assert _mine(api_client, board, doubting)["open"] == []

        detail = api_client.get(
            f"/spaces/{board['space_id']}/learning-analysis/class/points/"
            f"{board['point']}",
            headers=_auth(board["token"]),
        ).json()["data"]
        assert detail["lastPush"]["recipients"] == 3

        # 打开推来的那一组时当场出题，不算这个人自己的次数。
        gateway["answers"].append(_questions())
        opened = _start(api_client, board, stuck[0])
        assert opened.status_code == 200, opened.text
        assert opened.json()["data"]["source"] == "pushed"
        assert opened.json()["data"]["ready"] is True
        assert _mine(api_client, board, stuck[0])["usedToday"] == 0

    def test_fewer_than_three_stuck_is_not_pushed(
        self, user_client, api_client, _portal, db_session
    ):
        board = _board(user_client, api_client)
        for level in ("weak", "shaky"):
            _judged(
                _portal,
                db_session,
                board,
                _member(user_client, api_client, board),
                level,
            )
        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/class/points/"
            f"{board['point']}/push",
            headers=_auth(board["token"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {
            "pushed": False,
            "recipients": None,
            "minPeople": 3,
        }

        async def count() -> int:
            return int(
                await db_session.scalar(
                    select(func.count(SpaceLearningPractice.id)).where(
                        SpaceLearningPractice.space_id == board["space_id"]
                    )
                )
            )

        assert _portal.call(count) == 0

    def test_a_plain_member_cannot_push(
        self, user_client, api_client, _portal, db_session
    ):
        board = _board(user_client, api_client)
        ann = _member(user_client, api_client, board)
        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/class/points/"
            f"{board['point']}/push",
            headers=_auth(ann.token),
        )
        assert resp.status_code == 403, resp.text


class TestPracticeOutcome:
    def test_the_teacher_sees_how_many_moved_up_only_from_three_people(
        self, user_client, api_client, _portal, db_session
    ):
        board = _board(user_client, api_client)

        def practised(user, before: str, after: str) -> None:
            async def write() -> None:
                now = datetime.now(UTC)
                db_session.add(
                    SpaceLearningPractice(
                        space_id=board["space_id"],
                        user_id=user.user_id,
                        knowledge_point_id=board["point"],
                        source="self",
                        questions=QUESTIONS,
                        answers=[0, 0, 0],
                        correct=3,
                        level_before=before,
                        level_after=after,
                        created_at=now - timedelta(minutes=5),
                        generated_at=now - timedelta(minutes=5),
                        completed_at=now,
                    )
                )
                await db_session.flush()

            _portal.call(write)

        def detail() -> dict:
            resp = api_client.get(
                f"/spaces/{board['space_id']}/learning-analysis/class/points/"
                f"{board['point']}",
                headers=_auth(board["token"]),
            )
            assert resp.status_code == 200, resp.text
            return resp.json()["data"]

        people = [_member(user_client, api_client, board) for _ in range(3)]
        practised(people[0], "weak", "shaky")
        practised(people[1], "shaky", "shaky")
        assert detail()["practice"] is None
        practised(people[2], "shaky", "mastered")
        assert detail()["practice"] == {"practiced": 3, "improved": 2}
