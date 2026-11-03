"""The platform's tools, run against the real backend (结论 63).

A session calls `run_platform_tool` for every tool on `PLATFORM_TOOLS`; these
tests hand it a host whose requests go to this app with a room's own scoped
token, so what is checked is what the room actually gets — the stored message,
the refused write, the card — not a request someone expected to be sent.
"""

import importlib.util
import json
import re
import uuid
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness.channel import mint_session_token
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
        # The credential a session launches with (`mint_session_token`): its
        # room's agent, in this conversation.
        self.headers = {
            "Authorization": f"Bearer {
                mint_session_token(project, topic, room_agent_seat(client, topic))
            }"
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


@pytest.fixture
def task(client, room):
    """A task in that channel, where a living document is."""
    project, channel = room
    return project, open_task(client, channel, "", owner="alice")["id"]


def test_a_message_is_published_as_written(client, room):
    host = BackendHost(client, *room)
    content = "检查通过了。\n`$HOME` 和 $(echo hi) 是原文。"

    cheese.run_platform_tool("chat_send", {"content": content}, host)

    history = client.get(f"/topics/{room[1]}/blocks").json()["data"]["data"]
    assert [b["content"] for b in history if b["kind"] == "message"] == [content]
    listed = cheese.run_platform_tool("cheese_chat", {"action": "list"}, host)
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


def test_a_stale_write_to_the_living_doc_is_refused_with_the_way_out(client, task):
    """两条会话都读过第 N 版；先写的赢，后写的被拒并被告知怎么办。"""
    first = BackendHost(client, *task)
    second = BackendHost(client, *task)
    assert "还是空的" in cheese.run_platform_tool(
        "cheese_doc", {"action": "get"}, first
    )
    cheese.run_platform_tool("cheese_doc", {"action": "get"}, second)

    cheese.run_platform_tool(
        "cheese_doc", {"action": "set", "content": "# 第一版\n"}, first
    )
    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool(
            "cheese_doc", {"action": "set", "content": "# 另一个人的版本\n"}, second
        )

    assert "cheese_doc(action=get)" in str(refused.value)
    assert "第一版" in cheese.run_platform_tool("cheese_doc", {"action": "get"}, second)
    # Having read again, the second writer gets through.
    cheese.run_platform_tool(
        "cheese_doc", {"action": "set", "content": "# 另一个人的版本\n"}, second
    )
    assert "另一个人的版本" in cheese.run_platform_tool(
        "cheese_doc", {"action": "get"}, first
    )


def test_an_edit_changes_only_its_passage_and_a_suggestion_is_listed_apart(
    client, task
):
    host = BackendHost(client, *task)
    cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    cheese.run_platform_tool(
        "cheese_doc",
        {
            "action": "set",
            "content": "# 目标\n\n本周交初稿。\n\n## 范围\n\n只做前端。\n",
        },
        host,
    )

    said = cheese.run_platform_tool(
        "cheese_doc",
        {"action": "edit", "edits": [{"old": "本周交初稿。", "new": "周五交初稿。"}]},
        host,
    )
    assert "改了 1 处" in said
    cheese.run_platform_tool(
        "cheese_doc",
        {
            "action": "edit",
            "edits": [{"old": "只做前端。", "new": "前端和接口。"}],
            "suggest": True,
            "reason": "接口也在范围内",
        },
        host,
    )

    read = cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    text, _, pending = read.partition("待处理的修改建议")
    assert "周五交初稿。" in text and "只做前端。" in text
    assert "前端和接口。" not in text
    assert "前端和接口。" in pending and "接口也在范围内" in pending


def test_an_edit_whose_passage_is_gone_says_so_and_changes_nothing(client, task):
    host = BackendHost(client, *task)
    cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    cheese.run_platform_tool(
        "cheese_doc", {"action": "set", "content": "本周交初稿。\n"}, host
    )

    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool(
            "cheese_doc",
            {"action": "edit", "edits": [{"old": "下周", "new": "周五"}]},
            host,
        )

    assert "一处都没改" in str(refused.value)
    assert "cheese_doc(action=get)" in str(refused.value)
    assert (
        cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
        == "本周交初稿。\n"
    )


def test_a_suggestion_that_cannot_be_made_is_not_sent_back_to_reread(client, task):
    """Quoting the document again cannot turn a change a suggestion cannot
    mark (a row added to a table) into one it can, so the refusal says what
    can be done instead."""
    host = BackendHost(client, *task)
    cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    cheese.run_platform_tool(
        "cheese_doc", {"action": "set", "content": "本周交初稿。\n"}, host
    )
    client.collab.refuse_suggestions = True

    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool(
            "cheese_doc",
            {
                "action": "edit",
                "edits": [{"old": "本周交初稿。", "new": "本周交初稿。\n\n下周评审。"}],
                "suggest": True,
            },
            host,
        )

    said = str(refused.value)
    assert "一处都没改" in said
    assert "重读" not in said
    assert "直接改" in said and "在对话里说" in said
    assert (
        cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
        == "本周交初稿。\n"
    )


def test_an_ai_teammate_creates_the_task_it_was_asked_for(client, room):
    """`cheese_task(action=open)` creates the task for the person who asked for it. Creating
    one needs no machine."""
    project, topic = room
    host = BackendHost(client, project, topic)

    said = cheese.run_platform_tool(
        "cheese_task",
        {"action": "open", "title": "数据清洗", "brief": "按新口径", "owner": "alice"},
        host,
    )

    assert "数据清洗" in said
    assert host.synced == []
    [task] = client.get(f"/topics/{topic}/tasks").json()["data"]["data"]
    assert task["title"] == "数据清洗"
    assert task["owner_handle"] == "alice"


def test_a_title_too_wide_is_said_back_to_the_teammate_instead_of_cut(client, room):
    """侧栏放不下的标题，后端拒绝、CLI 把平台原话交给 AI 改短。工具报的是
    下一步怎么走（再调一次 `cheese_task(action=open)`），不是一句「失败了」。"""
    project, topic = room
    host = BackendHost(client, project, topic)

    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool(
            "cheese_task",
            {
                "action": "open",
                "title": "把这一条过于冗长的任务标题改短到侧栏一行里放得下",
                "brief": "按新口径",
                "owner": "alice",
            },
            host,
        )

    assert "48" in str(refused.value)
    assert "cheese_task(action=open)" in str(refused.value)
    assert client.get(f"/topics/{topic}/tasks").json()["data"]["data"] == []


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("cheese_room", {"action": "members"}),
        ("cheese_session", {"action": "status"}),
        ("cheese_library", {"action": "list"}),
    ],
)
def test_each_room_tool_is_accepted_by_the_backend(client, room, tool, arguments):
    """每一样的请求形状都是后端认的那一种：一个列在表上、后端却拒的工具，比没有更糟。"""
    assert cheese.run_platform_tool(tool, arguments, BackendHost(client, *room))


def test_a_task_session_names_its_task(client, room):
    """`cheese_room(action=rename)` names the task its session works in; a channel is named
    by whoever manages it, not by its AI teammate."""
    project, channel = room
    task = open_task(client, channel, "", owner="alice")["id"]
    host = BackendHost(client, project, task)
    assert cheese.run_platform_tool(
        "cheese_room", {"action": "rename", "text": "推荐原型"}, host
    )
    assert client.get(f"/topics/{task}/task").json()["data"]["title"] == "推荐原型"


def test_a_library_document_is_made_listed_and_changed_from_a_room(client, room):
    """芝士在频道里建一份资料库文档、列出来、点名改它：建的和改的都是那一份，
    频道里出现的是这份文档。"""
    project, topic = room
    host = BackendHost(client, project, topic)
    host.headers = {
        "Authorization": f"Bearer {
            mint_scoped_token(
                project_id=project,
                topic_id=topic,
                agent_handle=room_agent_seat(client, topic),
                access_scope='project',
            )
        }"
    }

    said = cheese.run_platform_tool(
        "cheese_doc",
        {"action": "new", "title": "竞品定价对比", "content": "三家都有年付"},
        host,
    )
    document = said.split("编号 ", 1)[1].split("（", 1)[0]
    assert "竞品定价对比" in cheese.run_platform_tool(
        "cheese_doc", {"action": "list"}, host
    )
    cheese.run_platform_tool(
        "cheese_doc",
        {
            "action": "edit",
            "document": document,
            "edits": [{"old": "年付", "new": "年付折扣"}],
        },
        host,
    )

    assert (
        cheese.run_platform_tool(
            "cheese_doc", {"action": "get", "document": document}, host
        )
        == "三家都有年付折扣"
    )
    # A channel has no living document of its own to have changed instead.
    with pytest.raises(cheese.PlatformHTTPError, match="channelHasNoDocument"):
        cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    lines = client.get(
        f"/topics/{topic}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert [
        b["meta"]["document"]["id"]
        for b in lines
        if (b.get("meta") or {}).get("document")
    ] == [document]


def test_a_document_made_in_a_thread_is_shown_in_that_thread(client, room):
    """芝士 answering in a 支线 makes a library document: the line with the
    document on it is in the 支线 it was made in, not in the channel's main
    line."""
    project, topic = room
    said = client.post(
        f"/topics/{topic}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "查一下竞品定价"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    thread = client.post(
        f"/blocks/{said['id']}/thread", headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    host = BackendHost(client, project, thread)
    host.headers = {
        "Authorization": f"Bearer {
            mint_scoped_token(
                project_id=project,
                topic_id=thread,
                agent_handle=room_agent_seat(client, topic),
                access_scope='project',
            )
        }"
    }

    cheese.run_platform_tool(
        "cheese_doc",
        {"action": "new", "title": "竞品定价对比", "content": "三家都有年付"},
        host,
    )

    def lines(conversation):
        blocks = client.get(
            f"/topics/{conversation}/blocks", headers=session_auth_headers("alice")
        ).json()["data"]["data"]
        return [b for b in blocks if (b.get("meta") or {}).get("document")]

    assert len(lines(thread)) == 1
    assert lines(topic) == []


def test_a_new_version_of_a_mini_app_is_what_the_document_then_shows(
    client, task, monkeypatch
):
    """Saving a new version moves the document's block onto it: an app saved
    and a document still naming the old version is the step a writer forgets."""
    from app.domain.web_snippet import check

    async def ran(code, shell):
        return check.Ran(heights={400: 200, 720: 180})

    monkeypatch.setattr(check, "run", ran)
    host = BackendHost(client, *task)

    made = cheese.run_platform_tool(
        "cheese_app", {"action": "save", "code": "<p>第一版</p>", "title": "试算"}, host
    )
    block = made[made.index(":::app") :].replace("一句话说明", "拖动看结果。")
    key = block.split()[1].split("@")[0]
    cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    cheese.run_platform_tool(
        "cheese_doc", {"action": "set", "content": f"正文\n\n{block}\n"}, host
    )

    said = cheese.run_platform_tool(
        "cheese_app", {"action": "save", "code": "<p>第二版</p>", "app": key}, host
    )

    assert "第 2 版" in said
    shown = cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    assert f":::app {key}@2 试算" in shown
    assert f"{key}@1" not in shown


def test_a_refused_edit_that_named_no_document_says_it_searched_this_ones(client, task):
    """Without `document` the edit goes to the conversation's own document. An
    agent that meant a library document reads 「原文找不到」 as a fault in the
    one it meant unless the refusal says which document was searched."""
    project, _ = task
    host = BackendHost(client, *task)
    cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    cheese.run_platform_tool(
        "cheese_doc", {"action": "set", "content": "本周交初稿。\n"}, host
    )
    made = cheese.run_platform_tool(
        "cheese_doc",
        {"action": "new", "title": "处置表", "content": "PR #2996。\n"},
        host,
    )
    library = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", made).group(0)

    with pytest.raises(cheese.PlatformToolError) as unnamed:
        cheese.run_platform_tool(
            "cheese_doc",
            {"action": "edit", "edits": [{"old": "PR #2996。", "new": "已合。"}]},
            host,
        )
    assert "没给 document" in str(unnamed.value)
    assert "实况文档" in str(unnamed.value)

    with pytest.raises(cheese.PlatformToolError) as named:
        cheese.run_platform_tool(
            "cheese_doc",
            {
                "action": "edit",
                "document": library,
                "edits": [{"old": "不在文里", "new": "x"}],
            },
            host,
        )
    assert "没给 document" not in str(named.value)
