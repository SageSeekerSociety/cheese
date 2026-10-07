"""The platform's own tools, run the way every harness runs them.

`run_platform_tool` is what a session calls for a tool on `PLATFORM_TOOLS`. What
it does is observable from two sides: the requests it hands the host (to the
backend, and to the machine for a file or a push) and the text the agent reads
back. These tests drive it through a host that records both and answers like the
backend would, so each one says what a call does, not how it is built.
"""

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
_ROOM = "11111111-1111-4111-8111-111111111111"
_DOC = "44444444-4444-4444-8444-444444444444"


def _load():
    loader = SourceFileLoader("cheese_platform_tools", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class Host:
    """Records what a tool asked for; answers with `answers[(method, path)]`."""

    def __init__(self, answers=None, *, environ=None, sync=None, envelope=None):
        # `envelope` 是后端在 `data` 之外捎回来的东西（今天只有文档的格式警告）。
        # 记在这里而不是塞进 `answers`：`answers` 是「这个地址答什么数据」，而
        # 警告跟的是哪一次响应，不是哪一个地址。
        self.envelope = envelope or {}
        self.environ = {
            "CHEESE_TOPIC": _ROOM,
            "CHEESE_PROJECT": "project-1",
            "CHEESE_AUTHOR": "cheese",
            **(environ or {}),
        }
        self.answers = answers or {}
        self.sync = sync
        self.doc_versions: dict = {}
        self.requests: list[dict] = []
        self.synced: list[str] = []

    def request(self, plan):
        self.requests.append(plan)
        answer = self.answers.get((plan["method"], plan["path"].split("?")[0]), {})
        if isinstance(answer, Exception):
            raise answer
        return {"data": answer, **self.envelope}

    def sync_task(self, task_id):
        self.synced.append(task_id)
        if self.sync is not None:
            raise self.sync


cheese_out_of_reach = "这台机器现在够不着"


def run(tool, args, host):
    return cheese.run_platform_tool(tool, args, host)


# --- chat reads ------------------------------------------------------------


def _message(number=1, **extra):
    return {
        "id": f"00000000-0000-0000-0000-{number:012d}",
        "topic_id": _ROOM,
        "kind": "message",
        "author": "alice",
        "author_type": "participant",
        "created_at": "2026-09-08T00:00:00Z",
        "content": "Use the revised dataset",
        "reply_to": None,
        "reactions": [],
        **extra,
    }


def _history(payload, room=_ROOM):
    return Host({("GET", f"/topics/{room}/history"): payload})


def test_default_chat_is_one_bounded_page():
    host = _history({"data": [_message()], "has_more": False})
    out = run("cheese_chat_list", {}, host)
    [plan] = host.requests
    parsed = urlsplit(plan["path"])
    assert plan["method"] == "GET"
    assert parsed.path == f"/topics/{_ROOM}/history"
    assert parse_qs(parsed.query)["limit"] == ["50"]
    assert "alice" in out and "Use the revised dataset" in out


def test_search_encodes_literal_query_and_preserves_scope():
    host = _history({"data": [], "has_more": False}, room="room-2")
    out = run(
        "cheese_chat_search",
        {"query": "报错 & 50%_", "topic": "room-2"},
        host,
    )
    asked = urlsplit(host.requests[0]["path"])
    assert asked.path == "/topics/room-2/history"
    assert parse_qs(asked.query)["q"] == ["报错 & 50%_"]
    assert "No messages" in out


@pytest.mark.parametrize(
    "kind", ["message", "attachment", "event", "comment", "doc_node", "future_kind"]
)
def test_special_messages_keep_content_metadata_replies_and_reactions(kind):
    block = _message(
        kind=kind,
        reply_to="parent-id",
        mime_type="application/pdf",
        anchor_quote="selected paragraph",
        refs=["source-id"],
        meta={"options": ["Yes", "No"], "answered": "Yes", "detail": "full error"},
        reactions=[{"emoji": "👍", "count": 2, "authors": ["bob", "carol"]}],
    )
    host = Host({("GET", f"/topics/{_ROOM}/history/{block['id']}"): block})
    out = run("cheese_chat_get", {"message_id": block["id"]}, host)
    for value in (
        kind,
        "parent-id",
        "application/pdf",
        "selected paragraph",
        "source-id",
        "answered",
        "full error",
        "👍",
        "bob",
        "carol",
    ):
        assert value in out


def test_replies_query_is_explicit_and_can_page():
    host = _history({"data": [_message(reply_to="parent-id")], "has_more": False})
    run(
        "cheese_chat_replies",
        {"message_id": "parent-id", "before": "cursor-id", "limit": 3},
        host,
    )
    params = parse_qs(urlsplit(host.requests[0]["path"]).query)
    assert params["reply_to"] == ["parent-id"]
    assert params["before"] == ["cursor-id"]
    assert params["limit"] == ["3"]


def test_chat_reads_answer_in_text_only():
    """The text is the whole answer: no raw-JSON mode is offered, and nested
    fields nobody has named yet still reach the reader."""
    for tool in (
        "cheese_chat_list",
        "cheese_chat_search",
        "cheese_chat_get",
        "cheese_chat_replies",
    ):
        schema = next(t for t in cheese.PLATFORM_TOOLS.schemas() if t["name"] == tool)
        assert "json" not in schema["inputSchema"]["properties"]
    block = _message(meta={"future": {"data": [1, "x"]}})
    host = Host({("GET", f"/topics/{_ROOM}/history/{block['id']}"): block})
    out = run("cheese_chat_get", {"message_id": block["id"]}, host)
    assert f"id={block['id']}" in out
    assert '"future": {"data": [1, "x"]}' in out


def test_long_message_can_be_read_without_losing_the_tail():
    block = _message(content="a" * 20000 + "END OF MESSAGE")
    host = Host({("GET", f"/topics/{_ROOM}/history/{block['id']}"): block})
    first = run("cheese_chat_get", {"message_id": block["id"]}, host)
    assert len(first) < 13000
    assert 'cheese_chat_get(message_id="' in first and "offset=12000" in first
    rest = run("cheese_chat_get", {"message_id": block["id"], "offset": 12000}, host)
    assert "END OF MESSAGE" in rest


def test_page_budget_keeps_newest_messages_and_a_real_next_cursor():
    blocks = [_message(i, content=f"message-{i}:" + "x" * 20000) for i in range(1, 51)]
    out = run("cheese_chat_list", {}, _history({"data": blocks, "has_more": False}))
    assert len(out) < 13000
    assert "message-50:" in out
    assert "cheese_chat_get(" in out
    assert "cheese_chat_list(" in out
    # The continuation must start before the oldest SHOWN message, not before
    # the server's entire page, or budget trimming would silently skip rows.
    shown = [b for b in blocks if f"message-{int(b['id'][-12:])}:" in out]
    assert f'before="{shown[0]["id"]}"' in out


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("cheese_chat_list", {"limit": 0}),
        ("cheese_chat_list", {"limit": 501}),
        ("cheese_chat_get", {"message_id": "id", "offset": -1}),
        ("cheese_chat_list", {"before": "a", "after": "b"}),
        ("cheese_chat_search", {}),
        ("cheese_chat_replies", {}),
    ],
)
def test_invalid_read_arguments_fail_before_any_request(tool, args):
    host = Host()
    with pytest.raises(cheese.PlatformToolError):
        run(tool, args, host)
    assert host.requests == []


# --- the living doc --------------------------------------------------------
#
# The living doc is replaced whole, so a write based on a version somebody has
# already moved past destroys their edit outright. The version is a fact about
# what the agent READ, never something it can state.
#
# A turn in a room knows the room, not its document: the tools ask the room
# which document is its own, then act on that.


def _doc_host(envelope=None, environ=None):
    return Host(
        {
            ("GET", f"/topics/{_ROOM}/document"): {"id": _DOC},
            ("GET", f"/documents/{_DOC}"): {
                "content": "# 现在的文档",
                "doc_version": 7,
            },
            ("PUT", f"/documents/{_DOC}"): {"doc_version": 8},
        },
        envelope=envelope,
        environ=environ,
    )


def _puts(host):
    return [p["body"] for p in host.requests if p["method"] == "PUT"]


def test_a_set_without_a_read_claims_no_version():
    """Never having read the doc is version 0 — which the platform accepts only
    when there is no doc yet."""
    host = _doc_host()
    run("cheese_doc_set", {"content": "# 我写的"}, host)
    assert _puts(host)[-1]["expected_version"] == 0
    assert _puts(host)[-1]["content"] == "# 我写的"


def test_a_set_writes_against_the_version_get_showed():
    host = _doc_host()
    assert "# 现在的文档" in run("cheese_doc_get", {}, host)
    run("cheese_doc_set", {"content": "# 我写的"}, host)
    assert _puts(host)[-1]["expected_version"] == 7


def test_a_won_set_remembers_the_version_it_produced():
    """Two writes in one turn is normal; the second is based on what the first
    produced."""
    host = _doc_host()
    run("cheese_doc_get", {}, host)
    run("cheese_doc_set", {"content": "# 我写的"}, host)
    run("cheese_doc_set", {"content": "# 我写的"}, host)
    assert [put["expected_version"] for put in _puts(host)] == [7, 8]


def test_a_refused_set_says_how_to_recover():
    """Retrying the same file is refused identically, forever — the way out is
    re-reading."""
    host = _doc_host()
    host.answers[("PUT", f"/documents/{_DOC}")] = cheese.PlatformHTTPError(
        409, json.dumps({"error": {"data": {"doc_version": 9}}})
    )
    with pytest.raises(cheese.PlatformToolError) as refused:
        run("cheese_doc_set", {"content": "# 我写的"}, host)
    assert "第 9 版" in str(refused.value)
    assert "cheese_doc_get" in str(refused.value)


def test_a_set_that_would_lose_text_shows_the_platforms_reason():
    """The platform names the line and the fix; that sentence is all the
    agent sees of a refused write, so it has to arrive whole."""
    host = _doc_host()
    reason = (
        "第 3 行是脚注定义（[^1]: 注），实况文档不支持脚注。"
        "请把脚注内容改成正文里的括注。"
    )
    host.answers[("PUT", f"/documents/{_DOC}")] = cheese.PlatformHTTPError(
        422, json.dumps({"error": {"message": reason, "data": {"line": 3}}})
    )
    with pytest.raises(cheese.PlatformToolError) as refused:
        run("cheese_doc_set", {"content": "# 我写的"}, host)
    assert reason in str(refused.value)
    assert "没有变" in str(refused.value)


def test_an_empty_doc_says_so_instead_of_answering_nothing():
    host = Host(
        {
            ("GET", f"/topics/{_ROOM}/document"): {"id": _DOC},
            ("GET", f"/documents/{_DOC}"): None,
        }
    )
    assert "还是空的" in run("cheese_doc_get", {}, host)


def test_a_turn_in_a_room_reads_and_writes_the_rooms_document():
    host = _doc_host()
    run("cheese_doc_get", {}, host)
    run("cheese_doc_set", {"content": "# 我写的"}, host)
    assert [(p["method"], p["path"]) for p in host.requests] == [
        ("GET", f"/topics/{_ROOM}/document"),
        ("GET", f"/documents/{_DOC}"),
        ("GET", f"/topics/{_ROOM}/document"),
        ("PUT", f"/documents/{_DOC}"),
    ]


def test_a_session_opened_on_a_document_needs_no_room():
    """A question asked in a document of the project's own runs with that
    document and no room at all."""
    host = _doc_host(environ={"CHEESE_DOCUMENT": _DOC, "CHEESE_TOPIC": ""})
    assert "# 现在的文档" in run("cheese_doc_get", {}, host)
    run("cheese_doc_set", {"content": "# 我写的"}, host)
    assert [(p["method"], p["path"]) for p in host.requests] == [
        ("GET", f"/documents/{_DOC}"),
        ("PUT", f"/documents/{_DOC}"),
    ]
    assert _puts(host)[-1]["expected_version"] == 7


def test_a_write_back_says_what_does_not_read_like_state():
    """写入照样成功，格式警告跟着这一次返回回来（#1889 第 3 条）。

    警告不回在别处：改的机会就是现在，而此刻在写字的人只看得见这次工具返回。
    """
    host = _doc_host(envelope={"warnings": ["正文 9000 字，超过 6000 字。"]})

    said = run("cheese_doc_set", {"content": "# 我写的"}, host)

    assert "已更新实况文档（第 8 版）" in said
    assert "正文 9000 字" in said


def test_a_clean_write_back_carries_no_warning():
    host = _doc_host()

    said = run("cheese_doc_set", {"content": "# 我写的"}, host)

    assert said == "已更新实况文档（第 8 版）。"


def test_a_document_is_written_with_the_machine_out_of_reach():
    """A task before its owner starts it reads its machine and writes nothing
    there, yet drafting its document is what it is there to do. A document is
    the platform's, so writing one asks nothing of the machine."""
    host = _doc_host()
    host.sync = RuntimeError(cheese_out_of_reach)

    said = run("cheese_doc_set", {"content": "# 我写的"}, host)

    assert said.startswith("已更新实况文档")
    assert _puts(host)[-1]["content"] == "# 我写的"
    assert host.synced == []


# --- tasks and acceptance --------------------------------------------------


def test_a_task_someone_asked_for_is_created_started_and_touches_nothing_else():
    """Asked to do something, an AI teammate creates the task and starts it for
    the person who asked. Creating one reads no file and pushes no branch."""
    host = Host(
        {
            ("POST", f"/topics/{_ROOM}/teammate-tasks"): {
                "id": "task-1",
                "title": "查一下分页",
                "owner_handle": "lisi",
                "started": True,
            }
        }
    )
    out = run(
        "cheese_task",
        {"title": "查一下分页", "summary": "干这个", "owner": "lisi", "start": True},
        host,
    )
    [plan] = host.requests
    assert plan["path"] == f"/topics/{_ROOM}/teammate-tasks"
    assert plan["body"]["start"] is True
    assert plan["body"]["owner_handle"] == "lisi"
    assert host.synced == []
    assert "查一下分页" in out


def test_a_task_that_could_not_start_tells_the_teammate_to_ask():
    host = Host(
        {
            ("POST", f"/topics/{_ROOM}/teammate-tasks"): {
                "id": "task-1",
                "title": "查一下分页",
                "owner_handle": "lisi",
                "started": False,
                "not_started_because": "没有审阅人",
            }
        }
    )
    out = run(
        "cheese_task",
        {"title": "查一下分页", "summary": "干这个", "start": True},
        host,
    )
    assert "没有审阅人" in out


def test_a_lock_someone_else_holds_is_a_refusal():
    host = Host(
        {
            ("POST", f"/topics/{_ROOM}/lock"): {
                "acquired": False,
                "reason": "任务 x 占着",
            }
        }
    )
    with pytest.raises(cheese.PlatformToolError, match="任务 x 占着"):
        run("cheese_lock", {}, host)
    assert host.requests[0]["body"] == {"kind": "heavy"}


# --- memory, roster, status and the rest -----------------------------------


def test_remember_is_gone_and_a_call_to_it_says_so():
    """`cheese_remember` 整个撤掉：写记忆改成直接写文件（见系统提示的记忆一节）。

    旧会话里还在调它的调用方，得到的是「没有这个工具」，而不是一句「已记入」——
    记进旧条目池的事实以后再也不会被注入任何地方，那是最坏的一种丢法。
    """
    assert "cheese_remember" not in set(cheese.PLATFORM_TOOLS.names())
    with pytest.raises(ValueError, match="Unknown platform tool: cheese_remember"):
        run("cheese_remember", {"fact": "x"}, Host())


def test_members_reads_the_current_topic_roster():
    host = Host(
        {
            ("GET", f"/topics/{_ROOM}/members"): {
                "data": [{"member_handle": "alice", "name": "Alice", "role": "owner"}]
            }
        }
    )
    assert "Alice（owner）→ 在消息里写 <@alice>" in run("cheese_members", {}, host)


def test_status_renders_the_snapshot():
    host = Host(
        {
            ("GET", f"/topics/{_ROOM}/status"): {
                "topic": {"title": "修东西", "status": "active"}
            }
        }
    )
    assert "修东西" in run("cheese_status", {}, host)


def test_notify_names_who_it_reached():
    host = Host(
        {("POST", "/projects/project-1/alerts"): {"data": [{"target_handle": "lisi"}]}}
    )
    assert "lisi" in run(
        "cheese_notify", {"title": "看下", "options": ["a", "b"]}, host
    )
    assert host.requests[0]["body"]["payload"] == {"options": ["a", "b"]}


def test_library_listing_names_each_file():
    host = Host(
        {
            ("GET", "/projects/project-1/library"): {
                "data": [
                    {"path": "预算表.xlsx", "bytes": 2048},
                ]
            }
        }
    )
    assert "预算表.xlsx\t2K" in run("cheese_library_ls", {}, host)
    assert "还没有文件" in run("cheese_library_ls", {}, Host())


@pytest.mark.parametrize(
    ("delivered", "expected"),
    [
        (True, "便条已递给那条线程。"),
        (False, "那条线程这会儿没有在跑的轮次,便条没人接住。"),
    ],
)
def test_note_says_whether_anyone_caught_it(delivered, expected):
    """便条直接进那条线程正在跑的那一轮，那边这一刻没在跑就没人接住。"""
    host = Host({("POST", f"/topics/{_ROOM}/note"): {"delivered": delivered}})
    assert run("cheese_note", {"thread": "t", "content": "看一眼 CI"}, host) == expected


def test_machine_reports_its_session_choice():
    host = Host(
        {
            ("PUT", f"/topics/{_ROOM}/compute-profile"): {
                "session": {"choice": {"name": "Workstation", "profile": "device"}}
            }
        }
    )
    out = run("cheese_machine", {"profile": "device", "device_id": "workstation"}, host)
    assert "Workstation" in out and "推送到分支" in out and "点头" not in out


def test_a_page_that_could_not_be_read_says_how_each_rung_failed():
    host = Host({("POST", "/fetch"): {"ok": False, "trail": "md:404 http:403"}})
    with pytest.raises(cheese.PlatformToolError, match="md:404 http:403"):
        run("cheese_fetch", {"url": "https://example.test"}, host)


def test_a_room_tool_refuses_without_a_room():
    host = Host(environ={"CHEESE_TOPIC": ""})
    with pytest.raises(cheese.PlatformToolError, match="CHEESE_TOPIC"):
        run(
            "cheese_feedback_propose",
            {"title": "t", "kind": "bug", "visibility": "public", "user_said": "x"},
            host,
        )
    assert host.requests == []


# ---------- the docs ----------


def test_a_docs_search_lists_where_to_read_next():
    host = Host(
        {
            ("POST", "/docs/agent/search"): {
                "dev": True,
                "hits": [
                    {
                        "title": "验收与采纳",
                        "heading": "采纳交付",
                        "url": "https://okcheese.com/docs/accept#is-merge",
                        "excerpt": "确认改动符合要求后点击「采纳」。",
                        "dev": False,
                    },
                    {
                        "title": "一轮",
                        "heading": "",
                        "url": "https://okcheese.com/docs/dev/turn",
                        "excerpt": "串行锁。",
                        "dev": True,
                    },
                ],
            }
        }
    )
    said = cheese.run_platform_tool("cheese_docs_search", {"query": "怎么采纳"}, host)
    assert host.requests[0]["body"] == {"query": "怎么采纳", "topic": _ROOM}
    assert "1. 验收与采纳 › 采纳交付" in said
    assert "https://okcheese.com/docs/accept#is-merge" in said
    assert "2. 一轮（开发文档）" in said
    assert "cheese_docs_read" in said


def test_an_empty_docs_search_says_to_rephrase_not_that_nothing_exists():
    host = Host({("POST", "/docs/agent/search"): {"dev": False, "hits": []}})
    said = cheese.run_platform_tool("cheese_docs_search", {"query": "火星"}, host)
    assert "换个说法" in said


def test_a_docs_read_returns_the_page_itself():
    host = Host(
        {("POST", "/docs/agent/read"): {"page": "accept", "markdown": "# 验收"}}
    )
    assert (
        cheese.run_platform_tool("cheese_docs_read", {"page": "accept"}, host)
        == "# 验收"
    )
    assert host.requests[0]["body"] == {"page": "accept", "topic": _ROOM}


# --- what a 芝士 answering someone looks up ---------------------------------


def test_a_memory_is_read_by_its_name_and_the_index_is_not_one():
    files = {
        "data": [
            {"path": "index.md", "content": "- [deploy](deploy.md)"},
            {"path": "deploy.md", "content": "部署走 CI。"},
        ],
        "total": 2,
    }
    host = Host({("GET", "/memory/files"): files})

    assert run("cheese_memory_read", {"name": "deploy.md"}, host) == "部署走 CI。"
    assert run("cheese_memory_read", {"name": "team/deploy.md"}, host) == "部署走 CI。"
    assert run("cheese_memory_read", {"name": "index.md"}, host) == "没有这一条记忆。"
    assert run("cheese_memory_read", {"name": "gone.md"}, host) == "没有这一条记忆。"


def test_an_attachment_is_read_as_text_up_to_a_limit():
    preview = ("GET", f"/topics/{_ROOM}/preview/file")
    long = "字" * (cheese._ATTACHMENT_CHARS + 5)

    text = run(
        "cheese_attachment_read", {"path": "a.md"}, Host({preview: {"content": "正文"}})
    )
    cut = run(
        "cheese_attachment_read", {"path": "a.md"}, Host({preview: {"content": long}})
    )
    binary = run(
        "cheese_attachment_read",
        {"path": "a.png"},
        Host({preview: {"content": None, "bytes": 2048}}),
    )

    assert text == "正文"
    assert cut.startswith("字" * cheese._ATTACHMENT_CHARS)
    assert len(cut) < len(long) + 40 and "只给了前" in cut
    assert "2048" in binary and "读不了" in binary


def test_a_project_search_says_when_nothing_was_found():
    search = ("GET", "/projects/project-1/context/search")
    empty = {"hits": {"records": [], "tasks": []}}
    found = {"hits": {"records": [{"room": "设计", "excerpt": "里程碑三号"}]}}

    assert "没有找到" in run(
        "cheese_project_search", {"query": "里程碑"}, Host({search: empty})
    )
    assert "里程碑三号" in run(
        "cheese_project_search", {"query": "里程碑"}, Host({search: found})
    )


def test_a_docs_search_from_no_room_names_no_room():
    host = Host(
        {("POST", "/docs/agent/search"): {"hits": []}}, environ={"CHEESE_TOPIC": ""}
    )

    run("cheese_docs_search", {"query": "验收"}, host)

    [plan] = host.requests
    assert "topic" not in plan["body"]


def test_a_message_time_is_read_as_utc_not_as_a_wall_clock():
    """The API answers with an instant; read bare, `07:52` looks like the time on
    someone's wall and gets copied into a document as such."""
    block = _message(created_at="2026-10-07T07:52:42.767338Z")
    out = run("cheese_chat_list", {}, _history({"data": [block], "has_more": False}))

    assert "2026-10-07 07:52:42 UTC" in out
    assert "T07:52" not in out


def test_a_time_with_an_offset_is_shown_in_utc():
    block = _message(created_at="2026-10-07T08:17:43.639592+08:00")
    host = Host({("GET", f"/topics/{_ROOM}/history/{block['id']}"): block})

    out = run("cheese_chat_get", {"message_id": block["id"]}, host)

    assert "2026-10-07 00:17:43 UTC" in out
