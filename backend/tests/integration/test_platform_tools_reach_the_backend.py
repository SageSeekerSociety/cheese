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
from tests.integration.conftest import post_project

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
    """A session host whose backend is this app, and whose machine holds `files`."""

    def __init__(self, client, project, topic, *, files=None):
        self.client = client
        self.environ = {
            "CHEESE_TOPIC": topic,
            "CHEESE_PROJECT": project,
            "CHEESE_AUTHOR": "cheese",
        }
        self.headers = {
            "X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=topic)
        }
        self.files = files or {}
        self.doc_versions: dict = {}
        self.synced: list[str] = []

    def request(self, plan):
        response = self.client.request(
            plan["method"], plan["path"], json=plan.get("body"), headers=self.headers
        )
        if not 200 <= response.status_code < 300:
            raise cheese.PlatformHTTPError(response.status_code, response.text)
        return response.json()

    def read_file(self, path):
        return self.files[path].encode()

    def sync_task(self, task_id):
        self.synced.append(task_id)


@pytest.fixture
def room(client):
    project = post_project(client, json={"name": "Tools"}).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work", "created_by": "alice"},
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
    first = BackendHost(client, *room, files={"d.md": "# 第一版\n"})
    second = BackendHost(client, *room, files={"d.md": "# 另一个人的版本\n"})
    assert "还没有实况文档" in cheese.run_platform_tool("cheese_doc_get", {}, first)
    cheese.run_platform_tool("cheese_doc_get", {}, second)

    cheese.run_platform_tool("cheese_doc_set", {"path": "d.md"}, first)
    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool("cheese_doc_set", {"path": "d.md"}, second)

    assert "cheese_doc_get" in str(refused.value)
    assert "第一版" in cheese.run_platform_tool("cheese_doc_get", {}, second)
    # Having read again, the second writer gets through.
    cheese.run_platform_tool("cheese_doc_set", {"path": "d.md"}, second)
    assert "另一个人的版本" in cheese.run_platform_tool("cheese_doc_get", {}, first)


def test_a_task_is_opened_without_the_machine(client, room):
    host = BackendHost(client, *room)

    said = cheese.run_platform_tool(
        "cheese_task",
        {"title": "数据清洗", "brief": "按新口径", "reviewer": "alice"},
        host,
    )

    task_id = said.split("id=", 1)[1].split("\n", 1)[0]
    assert f"cheese worktree {task_id}" in said
    assert "线程标识" in said
    assert host.synced == []
    closed = cheese.run_platform_tool(
        "cheese_close_task", {"task": task_id, "conclusion": "改由另一条做"}, host
    )
    assert "数据清洗" in closed


def test_a_remembered_fact_is_recalled(client, room):
    host = BackendHost(client, *room)
    cheese.run_platform_tool(
        "cheese_remember", {"fact": "部署走 blue-green 切换"}, host
    )
    assert "blue-green" in cheese.run_platform_tool(
        "cheese_recall", {"query": "部署 切换"}, host
    )


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("cheese_title", {"text": "推荐原型"}),
        ("cheese_decision", {"text": "用 item-based CF，数据稀疏"}),
        ("cheese_milestone", {"title": "中期汇报", "due": "2026-10-20"}),
        ("cheese_members", {}),
        ("cheese_status", {}),
        ("cheese_library_ls", {}),
    ],
)
def test_each_room_tool_is_accepted_by_the_backend(client, room, tool, arguments):
    """每一样的请求形状都是后端认的那一种：一个列在表上、后端却拒的工具，比没有更糟。"""
    assert cheese.run_platform_tool(tool, arguments, BackendHost(client, *room))
