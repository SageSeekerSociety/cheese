"""The platform's tools, run against the real backend (结论 63).

A session calls `run_platform_tool` for every tool on `PLATFORM_TOOLS`; these
tests hand it a host whose requests go to this app with a room's own scoped
token, so what is checked is what the room actually gets — the stored message,
the refused write, the card — not a request someone expected to be sent.
"""

import importlib.util
import json
import uuid
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_platform_tools", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class BackendHost:
    """A session host whose backend is this app."""

    def __init__(self, client, project, topic):
        self.client = client
        self.environ = {
            "CHEESE_TOPIC": topic,
            "CHEESE_PROJECT": project,
            "CHEESE_AUTHOR": "cheese",
        }
        self.headers = {
            "X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=topic)
        }
        self.doc_versions: dict = {}
        self.synced: list[str] = []

    def request(self, plan):
        response = self.client.request(
            plan["method"], plan["path"], json=plan.get("body"), headers=self.headers
        )
        if not 200 <= response.status_code < 300:
            raise cheese.PlatformHTTPError(response.status_code, response.text)
        return response.json()

    def sync_task(self, task_id):
        self.synced.append(task_id)


@pytest.fixture
def room(client):
    project = post_project(client, json={"name": "Tools"}, owner="alice").json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return project["id"], topic["id"]


def test_a_message_is_published_as_written(client, room):
    host = BackendHost(client, *room)
    content = "检查通过了。\n`$HOME` 和 $(echo hi) 是原文。"

    cheese.run_platform_tool("chat_send", {"content": content}, host)

    history = client.get(f"/topics/{room[1]}/blocks").json()["data"]["data"]
    assert [b["content"] for b in history if b["kind"] == "message"] == [content]
    listed = cheese.run_platform_tool("cheese_chat_list", {}, host)
    assert "检查通过了。" in listed


def test_an_explicit_chat_retry_keeps_the_message_after_a_lost_response(client, room):
    class LostResponseHost(BackendHost):
        calls = 0
        responses = []

        def request(self, plan):
            self.calls += 1
            response = super().request(plan)
            self.responses.append(response)
            if self.calls == 1:
                raise ConnectionError("response lost after the message committed")
            return response

    host = LostResponseHost(client, *room)
    arguments = {"content": "Deliver this once", "request_id": str(uuid.uuid4())}
    with pytest.raises(ConnectionError, match="response lost"):
        cheese.run_platform_tool("chat_send", arguments, host)
    assert host.calls == 1

    first_id = host.responses[0]["data"]["id"]
    retried = json.loads(cheese.run_platform_tool("chat_send", arguments, host))
    assert host.calls == 2
    history = client.get(f"/topics/{room[1]}/blocks").json()["data"]["data"]
    messages = [b for b in history if b["kind"] == "message"]
    assert [b["content"] for b in messages] == [arguments["content"]]
    assert messages[0]["id"] == retried["id"] == first_id


def test_a_stale_write_to_the_living_doc_is_refused_with_the_way_out(client, room):
    """两条会话都读过第 N 版；先写的赢，后写的被拒并被告知怎么办。"""
    first = BackendHost(client, *room)
    second = BackendHost(client, *room)
    assert "还是空的" in cheese.run_platform_tool("cheese_doc_get", {}, first)
    cheese.run_platform_tool("cheese_doc_get", {}, second)

    cheese.run_platform_tool("cheese_doc_set", {"content": "# 第一版\n"}, first)
    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool(
            "cheese_doc_set", {"content": "# 另一个人的版本\n"}, second
        )

    assert "cheese_doc_get" in str(refused.value)
    assert "第一版" in cheese.run_platform_tool("cheese_doc_get", {}, second)
    # Having read again, the second writer gets through.
    cheese.run_platform_tool(
        "cheese_doc_set", {"content": "# 另一个人的版本\n"}, second
    )
    assert "另一个人的版本" in cheese.run_platform_tool("cheese_doc_get", {}, first)


def test_an_edit_changes_only_its_passage_and_a_suggestion_is_listed_apart(
    client, room
):
    host = BackendHost(client, *room)
    cheese.run_platform_tool("cheese_doc_get", {}, host)
    cheese.run_platform_tool(
        "cheese_doc_set",
        {"content": "# 目标\n\n本周交初稿。\n\n## 范围\n\n只做前端。\n"},
        host,
    )

    said = cheese.run_platform_tool(
        "cheese_doc_edit",
        {"edits": [{"old": "本周交初稿。", "new": "周五交初稿。"}]},
        host,
    )
    assert "改了 1 处" in said
    cheese.run_platform_tool(
        "cheese_doc_edit",
        {
            "edits": [{"old": "只做前端。", "new": "前端和接口。"}],
            "suggest": True,
            "reason": "接口也在范围内",
        },
        host,
    )

    read = cheese.run_platform_tool("cheese_doc_get", {}, host)
    text, _, pending = read.partition("待处理的修改建议")
    assert "周五交初稿。" in text and "只做前端。" in text
    assert "前端和接口。" not in text
    assert "前端和接口。" in pending and "接口也在范围内" in pending


def test_an_edit_whose_passage_is_gone_says_so_and_changes_nothing(client, room):
    host = BackendHost(client, *room)
    cheese.run_platform_tool("cheese_doc_get", {}, host)
    cheese.run_platform_tool("cheese_doc_set", {"content": "本周交初稿。\n"}, host)

    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool(
            "cheese_doc_edit", {"edits": [{"old": "下周", "new": "周五"}]}, host
        )

    assert "一处都没改" in str(refused.value)
    assert "cheese_doc_get" in str(refused.value)
    assert cheese.run_platform_tool("cheese_doc_get", {}, host) == "本周交初稿。\n"


def test_a_task_is_proposed_and_a_person_creates_it(client, room):
    """芝士不能自己创建任务：`cheese_task` 在房间里放一张提议卡，点了创建的人才是
    负责人。提议用不到机器。"""
    project, topic = room
    host = BackendHost(client, project, topic)

    said = cheese.run_platform_tool(
        "cheese_task", {"title": "数据清洗", "summary": "按新口径"}, host
    )

    assert "数据清洗" in said
    assert host.synced == []
    tasks = client.get(f"/topics/{topic}/tasks").json()["data"]["data"]
    assert tasks == []
    [proposal] = client.get(
        f"/topics/{topic}/task-proposals", headers=session_auth_headers("alice")
    ).json()["data"]

    created = client.post(
        f"/topics/{topic}/task-proposals/{proposal['id']}/accept",
        headers=session_auth_headers("alice"),
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["title"] == "数据清洗"
    assert created.json()["data"]["owner_handle"] == "alice"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("cheese_members", {}),
        ("cheese_status", {}),
        ("cheese_library_ls", {}),
    ],
)
def test_each_room_tool_is_accepted_by_the_backend(client, room, tool, arguments):
    """每一样的请求形状都是后端认的那一种：一个列在表上、后端却拒的工具，比没有更糟。"""
    assert cheese.run_platform_tool(tool, arguments, BackendHost(client, *room))


def test_a_task_session_names_its_task(client, room):
    """`cheese_title` names the task its session works in; a channel is named
    by whoever manages it, not by its AI teammate."""
    project, channel = room
    task = open_task(client, channel, "", owner="alice", start=False)["id"]
    host = BackendHost(client, project, task)
    assert cheese.run_platform_tool("cheese_title", {"text": "推荐原型"}, host)
    assert client.get(f"/topics/{task}/task").json()["data"]["title"] == "推荐原型"


def test_a_library_document_is_made_listed_and_changed_from_a_room(client, room):
    """芝士在话题里建一份资料库文档、列出来、点名改它：建的和改的都是那一份，
    话题里出现的是这份文档，话题自己的实况文档一字没动。"""
    project, topic = room
    host = BackendHost(client, project, topic)
    host.headers = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project,
            topic_id=topic,
            agent_handle=room_agent_seat(client, topic),
            access_scope="project",
        )
    }

    said = cheese.run_platform_tool(
        "cheese_doc_new", {"title": "竞品定价对比", "content": "三家都有年付"}, host
    )
    document = said.split("编号 ", 1)[1].split("（", 1)[0]
    assert "竞品定价对比" in cheese.run_platform_tool("cheese_doc_list", {}, host)
    cheese.run_platform_tool(
        "cheese_doc_edit",
        {"document": document, "edits": [{"old": "年付", "new": "年付折扣"}]},
        host,
    )

    assert (
        cheese.run_platform_tool("cheese_doc_get", {"document": document}, host)
        == "三家都有年付折扣"
    )
    assert "还是空的" in cheese.run_platform_tool("cheese_doc_get", {}, host)
    lines = client.get(
        f"/topics/{topic}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert [
        b["meta"]["document"]["id"]
        for b in lines
        if (b.get("meta") or {}).get("document")
    ] == [document]
