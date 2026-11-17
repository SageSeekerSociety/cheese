"""学情分析（step 1 的底座）：开关、知识点清单、告知卡、题目上的知识点、AI 建议。

规则都是先写下来、再写的代码，逐条按浏览器会收到的状态码与 JSON 来断：

- **开关由管理员按**（``edit_settings``）：所有者、管理员按得动；成员按不动（403）；
  板外的人读都读不到（404，``require_reviewed_space`` 接住）；
- **告知卡**：板开着且这个人没有确认记录才要弹。本来就在板里的人、开关打开后才加入的
  人一视同仁 —— 都是下一次进来看到它。点一次「我已阅读并知悉」之后永远不再弹；再点一次
  不产生第二行；
- **知识点清单**：加、改名、移除（软删）；同名（还 live 的）拒绝；移除后同名还能再加
  回来；移除后不留硬痕（行还在，已经归在它下面的题还显示这个名字）；
- **题目上的知识点**：建题与改题都带 ``knowledgePointIds``，整份替换；不属于本板的 id
  当它不存在（不报错，也不挂上去）；读列表与单题都能拿到 ``knowledgePoints``；
- **AI 建议**：网关是 MockTransport（签发 key 与 completion 都走它），把模型回的一个
  JSON 对象翻成建议清单，``isNew`` 由服务端拿现有清单算。

这条用例的网关 fixture 与 ``test_bookmark_naming.py`` 同一个形状：mint 和 completion
都从这个 transport 走，所以没有任何一处真的连去 LiteLLM。
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.space.models import SpaceLearningNotice
from app.domain.usage import pricing
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块过了审的板，外加建造者的 token 与它的默认分类。"""
    creator = user_client.create_user()
    token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Learning Analysis Board ({suffix})",
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
    return {
        "creator": creator,
        "token": token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _read(api_client: TestClient, token: str, space_id: int) -> dict:
    resp = api_client.get(f"/spaces/{space_id}/learning-analysis", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _set_switch(
    api_client: TestClient, token: str, space_id: int, enabled: bool
) -> dict:
    resp = api_client.patch(
        f"/spaces/{space_id}/learning-analysis",
        json={"enabled": enabled},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["learningAnalysis"]


def _add_point(api_client: TestClient, token: str, space_id: int, name: str) -> dict:
    resp = api_client.post(
        f"/spaces/{space_id}/learning-analysis/knowledge-points",
        json={"name": name},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["knowledgePoint"]


def _point_names(data: dict) -> list[str]:
    return [p["name"] for p in data["knowledgePoints"]]


def _member(api_client: TestClient, board: dict, user) -> str:
    """把 ``user`` 加进这块板，返回它的 token。"""
    added = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["token"]),
    )
    assert added.status_code == 201, added.text


def _publish(
    api_client: TestClient,
    board: dict,
    *,
    token: str,
    name: str,
    point_ids: list[int] | None = None,
) -> dict:
    body = {
        "mentors": [],
        "name": name,
        "intro": "题",
        "description": '{"type":"doc","content":[]}',
        "space": board["space_id"],
        "categoryId": board["category_id"],
        "submitterType": "USER",
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": 4_000_000_000_000,
        "knowledgePointIds": point_ids or [],
    }
    return api_client.post("/tasks", json=body, headers=_auth(token))


class TestTheSwitch:
    def test_a_member_cannot_turn_it_on(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        member = user_client.create_user()
        _member(api_client, board, member)
        member_token = _login(user_client, api_client, member)

        refused = api_client.patch(
            f"/spaces/{board['space_id']}/learning-analysis",
            json={"enabled": True},
            headers=_auth(member_token),
        )
        assert refused.status_code == 403, refused.text

    def test_a_stranger_cannot_read_it(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)
        resp = api_client.get(
            f"/spaces/{board['space_id']}/learning-analysis",
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 404, resp.text

    def test_turning_it_on_records_who_and_when(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        assert (
            _read(api_client, board["token"], board["space_id"])["learningAnalysis"][
                "enabled"
            ]
            is False
        )

        switch = _set_switch(api_client, board["token"], board["space_id"], True)
        assert switch["enabled"] is True
        assert switch["enabledBy"] == board["creator"].user_id
        assert isinstance(switch["enabledAt"], int)

    def test_the_creator_can_manage_and_a_member_cannot(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        member = user_client.create_user()
        _member(api_client, board, member)
        member_token = _login(user_client, api_client, member)

        assert _read(api_client, board["token"], board["space_id"])["canManage"] is True
        assert _read(api_client, member_token, board["space_id"])["canManage"] is False


class TestTheKnowledgePointList:
    def test_add_rename_and_remove(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        first = _add_point(api_client, board["token"], board["space_id"], "相关子查询")
        _add_point(api_client, board["token"], board["space_id"], "索引")
        assert _point_names(_read(api_client, board["token"], board["space_id"])) == [
            "相关子查询",
            "索引",
        ]

        renamed = api_client.patch(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points/{first['id']}",
            json={"name": "标量子查询"},
            headers=_auth(board["token"]),
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["data"]["knowledgePoint"]["name"] == "标量子查询"

        removed = api_client.delete(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points/{first['id']}",
            headers=_auth(board["token"]),
        )
        assert removed.status_code == 204, removed.text
        assert _point_names(_read(api_client, board["token"], board["space_id"])) == [
            "索引"
        ]

    def test_a_duplicate_live_name_is_refused(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        _add_point(api_client, board["token"], board["space_id"], "索引")
        again = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points",
            json={"name": "索引"},
            headers=_auth(board["token"]),
        )
        assert again.status_code == 400, again.text

    def test_a_removed_name_can_be_added_back(
        self, user_client: UserCreator, api_client: TestClient
    ):
        """软删：同一个名字以后还能再加回来（偏唯一索引只管 live 的行）。"""
        board = _board(user_client, api_client)
        point = _add_point(api_client, board["token"], board["space_id"], "索引")
        assert (
            api_client.delete(
                f"/spaces/{board['space_id']}/learning-analysis/knowledge-points/"
                f"{point['id']}",
                headers=_auth(board["token"]),
            ).status_code
            == 204
        )
        readded = _add_point(api_client, board["token"], board["space_id"], "索引")
        assert readded["name"] == "索引"
        assert readded["id"] != point["id"]

    def test_a_blank_name_is_refused(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points",
            json={"name": "   "},
            headers=_auth(board["token"]),
        )
        assert resp.status_code == 400, resp.text

    def test_a_member_cannot_edit_the_list(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        member = user_client.create_user()
        _member(api_client, board, member)
        member_token = _login(user_client, api_client, member)
        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points",
            json={"name": "成员加的"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403, resp.text


class TestTheJoinNotice:
    def _notice(self, api_client: TestClient, token: str, space_id: int) -> dict:
        resp = api_client.get(
            f"/spaces/{space_id}/learning-analysis/notice", headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    def test_it_does_not_pop_while_the_analysis_is_off(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        state = self._notice(api_client, board["token"], board["space_id"])
        assert state == {
            "enabled": False,
            "needsAcknowledge": False,
            "acknowledged": False,
        }

    def test_someone_already_in_the_board_sees_it_on_the_next_entry(
        self, user_client: UserCreator, api_client: TestClient
    ):
        """开关打开前就在板里的人，下一次进来也要看到它 —— 与后来加入的人一视同仁。"""
        board = _board(user_client, api_client)
        member = user_client.create_user()
        _member(api_client, board, member)
        member_token = _login(user_client, api_client, member)

        _set_switch(api_client, board["token"], board["space_id"], True)
        assert (
            self._notice(api_client, member_token, board["space_id"])[
                "needsAcknowledge"
            ]
            is True
        )

    def test_acknowledging_clears_it_for_good(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        _set_switch(api_client, board["token"], board["space_id"], True)
        assert (
            self._notice(api_client, board["token"], board["space_id"])[
                "needsAcknowledge"
            ]
            is True
        )

        ack = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/notice",
            headers=_auth(board["token"]),
        )
        assert ack.status_code == 200, ack.text
        assert ack.json()["data"] == {"acknowledged": True}

        after = self._notice(api_client, board["token"], board["space_id"])
        assert after["needsAcknowledge"] is False and after["acknowledged"] is True

    def test_clicking_twice_leaves_one_record(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        board = _board(user_client, api_client)
        _set_switch(api_client, board["token"], board["space_id"], True)
        for _ in range(2):
            resp = api_client.post(
                f"/spaces/{board['space_id']}/learning-analysis/notice",
                headers=_auth(board["token"]),
            )
            assert resp.status_code == 200, resp.text

        async def _count() -> int:
            rows = await db_session.scalars(
                select(SpaceLearningNotice.id).where(
                    SpaceLearningNotice.space_id == board["space_id"],
                    SpaceLearningNotice.user_id == board["creator"].user_id,
                )
            )
            return len(list(rows))

        assert _portal.call(_count) == 1


class TestATasksKnowledgePoints:
    def test_publishing_files_the_task_under_its_points(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        a = _add_point(api_client, board["token"], board["space_id"], "相关子查询")
        b = _add_point(api_client, board["token"], board["space_id"], "索引")

        published = _publish(
            api_client,
            board,
            token=board["token"],
            name="一道题",
            point_ids=[a["id"], b["id"]],
        )
        assert published.status_code == 200, published.text
        task = published.json()["data"]["task"]
        assert [p["name"] for p in task["knowledgePoints"]] == ["相关子查询", "索引"]

        # 列表页也读得到（整页一次查询填上，不是 undefined）。
        listed = api_client.get(
            "/tasks",
            params={"space": board["space_id"]},
            headers=_auth(board["token"]),
        )
        assert listed.status_code == 200, listed.text
        row = next(t for t in listed.json()["data"]["tasks"] if t["id"] == task["id"])
        assert [p["name"] for p in row["knowledgePoints"]] == ["相关子查询", "索引"]

    def test_editing_replaces_the_whole_set(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        a = _add_point(api_client, board["token"], board["space_id"], "A")
        b = _add_point(api_client, board["token"], board["space_id"], "B")
        c = _add_point(api_client, board["token"], board["space_id"], "C")
        published = _publish(
            api_client, board, token=board["token"], name="题", point_ids=[a["id"]]
        )
        task_id = published.json()["data"]["task"]["id"]

        patched = api_client.patch(
            f"/tasks/{task_id}",
            json={"knowledgePointIds": [b["id"], c["id"]]},
            headers=_auth(board["token"]),
        )
        assert patched.status_code == 200, patched.text
        assert [
            p["name"] for p in patched.json()["data"]["task"]["knowledgePoints"]
        ] == [
            "B",
            "C",
        ]

    def test_a_point_from_another_board_is_dropped(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        other = _board(user_client, api_client)
        mine = _add_point(api_client, board["token"], board["space_id"], "我的")
        theirs = _add_point(api_client, other["token"], other["space_id"], "别处的")

        published = _publish(
            api_client,
            board,
            token=board["token"],
            name="题",
            point_ids=[mine["id"], theirs["id"]],
        )
        assert published.status_code == 200, published.text
        assert [
            p["name"] for p in published.json()["data"]["task"]["knowledgePoints"]
        ] == ["我的"]

    def test_a_removed_point_leaves_the_tasks_list(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        _set_switch(api_client, board["token"], board["space_id"], True)
        point = _add_point(api_client, board["token"], board["space_id"], "临时")
        published = _publish(
            api_client,
            board,
            token=board["token"],
            name="题",
            point_ids=[point["id"]],
        )
        task_id = published.json()["data"]["task"]["id"]
        before = api_client.get(f"/tasks/{task_id}", headers=_auth(board["token"]))
        assert [
            p["name"] for p in before.json()["data"]["task"]["knowledgePoints"]
        ] == ["临时"]

        api_client.delete(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points/"
            f"{point['id']}",
            headers=_auth(board["token"]),
        )
        read = api_client.get(f"/tasks/{task_id}", headers=_auth(board["token"]))
        assert read.status_code == 200, read.text
        # 软删掉的知识点不再出现在这道题的清单里（关联行还留着）。
        assert read.json()["data"]["task"]["knowledgePoints"] == []

    def test_switching_off_hides_a_tasks_points_without_dropping_them(
        self, user_client: UserCreator, api_client: TestClient
    ):
        board = _board(user_client, api_client)
        _set_switch(api_client, board["token"], board["space_id"], True)
        point = _add_point(api_client, board["token"], board["space_id"], "索引")
        published = _publish(
            api_client,
            board,
            token=board["token"],
            name="题",
            point_ids=[point["id"]],
        )
        task_id = published.json()["data"]["task"]["id"]

        def _detail_points() -> list[str]:
            read = api_client.get(f"/tasks/{task_id}", headers=_auth(board["token"]))
            assert read.status_code == 200, read.text
            return [p["name"] for p in read.json()["data"]["task"]["knowledgePoints"]]

        _set_switch(api_client, board["token"], board["space_id"], False)
        assert _detail_points() == []

        _set_switch(api_client, board["token"], board["space_id"], True)
        assert _detail_points() == ["索引"]


@pytest.fixture
def gateway(monkeypatch):
    """一个签发 key、并按测试排好的话作答的网关（MockTransport，不连 LiteLLM）。

    路由自己不带 transport（它调 ``suggest`` 时不传），所以照 ``test_task_naming.py``
    的做法把 ``httpx.AsyncClient`` 换成一个默认挂上这个 transport 的子类 —— 网关的
    每一次调用（mint 与 completion）都从这个 transport 走。
    """
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
                headers={"x-litellm-response-cost": "0.0001"},
                json={
                    "choices": [
                        {"message": {"content": answer}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 20},
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
    # 计价表按 5 分钟缓存，跨用例会串味（同 test_docs_site.py）。
    pricing.forget()
    return seen


class TestTheAiSuggestion:
    def test_it_suggests_and_marks_what_is_already_on_the_list(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        gateway,
    ):
        """建议里的 ``isNew`` 由服务端拿现有清单算：清单里已有的报成新的也错不了。"""
        board = _board(user_client, api_client)
        _add_point(api_client, board["token"], board["space_id"], "索引")
        gateway["answers"].append(
            json.dumps(
                {
                    "suggestions": [
                        {"name": "索引", "why": "已经有", "confidence": "high"},
                        {"name": "相关子查询", "why": "题面要用", "confidence": "high"},
                    ]
                }
            )
        )

        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points/"
            "suggestions",
            json={"name": "第 3 题", "description": "写一条相关子查询"},
            headers=_auth(board["token"]),
        )
        assert resp.status_code == 200, resp.text
        suggestions = resp.json()["data"]["suggestions"]
        assert [(s["name"], s["isNew"]) for s in suggestions] == [
            ("索引", False),
            ("相关子查询", True),
        ]
        # 现有的清单名字进了给模型读的那份材料。
        assert "- 索引" in gateway["asked"][0]

    def test_a_gateway_that_cannot_be_reached_says_so(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        monkeypatch,
    ):
        """取不到 key 是「学情分析暂时用不了」（503），不是 500。"""
        board = _board(user_client, api_client)
        monkeypatch.setattr(settings, "llm_gateway_admin_base", "")
        monkeypatch.setattr(settings, "llm_gateway_admin_key", "")
        resp = api_client.post(
            f"/spaces/{board['space_id']}/learning-analysis/knowledge-points/"
            "suggestions",
            json={"name": "题", "description": ""},
            headers=_auth(board["token"]),
        )
        assert resp.status_code == 503, resp.text
