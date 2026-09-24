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
_TASK = "33333333-3333-4333-8333-333333333333"


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

    def __init__(self, answers=None, *, files=None, environ=None, sync=None):
        self.environ = {
            "CHEESE_TOPIC": _ROOM,
            "CHEESE_PROJECT": "project-1",
            "CHEESE_AUTHOR": "cheese",
            **(environ or {}),
        }
        self.answers = answers or {}
        self.files = files or {}
        self.sync = sync
        self.doc_versions: dict = {}
        self.requests: list[dict] = []
        self.synced: list[str] = []
        self.read: list[str] = []

    def request(self, plan):
        self.requests.append(plan)
        answer = self.answers.get((plan["method"], plan["path"].split("?")[0]), {})
        if isinstance(answer, Exception):
            raise answer
        return {"data": answer}

    def read_file(self, path):
        self.read.append(path)
        if path not in self.files:
            raise RuntimeError(cheese_out_of_reach)
        return self.files[path].encode()

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
        {"query": "报错 & 50%_", "topic": "room-2", "task": "card-1"},
        host,
    )
    query = parse_qs(urlsplit(host.requests[0]["path"]).query)
    assert query["q"] == ["报错 & 50%_"]
    assert query["task_id"] == ["card-1"]
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


def test_json_preserves_every_field():
    block = _message(meta={"future": {"data": [1, "x"]}}, content="x" * 20000)
    host = Host({("GET", f"/topics/{_ROOM}/history/{block['id']}"): block})
    assert (
        json.loads(
            run("cheese_chat_get", {"message_id": block["id"], "json": True}, host)
        )
        == block
    )


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


def _doc_host(files=None):
    return Host(
        {
            ("GET", f"/topics/{_ROOM}/doc"): {
                "content": "# 现在的文档",
                "doc_version": 7,
            },
            ("PUT", f"/topics/{_ROOM}/doc"): {"doc_version": 8},
        },
        files={"notes/d.md": "# 我写的"} if files is None else files,
    )


def _puts(host):
    return [p["body"] for p in host.requests if p["method"] == "PUT"]


def test_a_set_without_a_read_claims_no_version():
    """Never having read the doc is version 0 — which the platform accepts only
    when there is no doc yet."""
    host = _doc_host()
    run("cheese_doc_set", {"path": "notes/d.md"}, host)
    assert _puts(host)[-1]["expected_version"] == 0
    assert _puts(host)[-1]["content"] == "# 我写的"


def test_a_set_writes_against_the_version_get_showed():
    host = _doc_host()
    assert "# 现在的文档" in run("cheese_doc_get", {}, host)
    run("cheese_doc_set", {"path": "notes/d.md"}, host)
    assert _puts(host)[-1]["expected_version"] == 7


def test_a_won_set_remembers_the_version_it_produced():
    """Two writes in one turn is normal; the second is based on what the first
    produced."""
    host = _doc_host()
    run("cheese_doc_get", {}, host)
    run("cheese_doc_set", {"path": "notes/d.md"}, host)
    run("cheese_doc_set", {"path": "notes/d.md"}, host)
    assert [put["expected_version"] for put in _puts(host)] == [7, 8]


def test_a_refused_set_says_how_to_recover():
    """Retrying the same file is refused identically, forever — the way out is
    re-reading."""
    host = _doc_host()
    host.answers[("PUT", f"/topics/{_ROOM}/doc")] = cheese.PlatformHTTPError(
        409, json.dumps({"error": {"data": {"doc_version": 9}}})
    )
    with pytest.raises(cheese.PlatformToolError) as refused:
        run("cheese_doc_set", {"path": "notes/d.md"}, host)
    assert "第 9 版" in str(refused.value)
    assert "cheese_doc_get" in str(refused.value)


def test_an_empty_doc_says_so_instead_of_answering_nothing():
    host = Host({("GET", f"/topics/{_ROOM}/doc"): None})
    assert "还没有实况文档" in run("cheese_doc_get", {}, host)


def test_a_file_the_machine_cannot_give_writes_nothing():
    host = _doc_host(files={})
    with pytest.raises(RuntimeError, match=cheese_out_of_reach):
        run("cheese_doc_set", {"path": "notes/d.md"}, host)
    assert _puts(host) == []


# --- tasks and acceptance --------------------------------------------------


def test_task_creates_the_card_and_nothing_else():
    """开活只动平台：不读文件、不推分支。线程标识和准备目录的那一步都在返回里。"""
    host = Host(
        {
            ("POST", f"/topics/{_ROOM}/split"): {
                "id": _TASK,
                "title": "查一下分页",
                "thread_label": f"work-{_TASK}",
                "reviewer_handle": "lisi",
            }
        }
    )
    out = run("cheese_task", {"title": "查一下分页", "brief": "干这个"}, host)
    [plan] = host.requests
    assert plan["path"] == f"/topics/{_ROOM}/split"
    assert plan["body"] == {
        "title": "查一下分页",
        "created_by": "cheese",
        "brief": "干这个",
    }
    assert host.synced == [] and host.read == []
    assert f"work-{_TASK}" in out and "线程标识" in out
    assert f"cheese worktree {_TASK}" in out
    assert "lisi" in out


def test_close_task_names_the_thread_it_is_about():
    host = Host(
        {
            ("POST", f"/topics/{_ROOM}/tasks/{_TASK}/close"): {
                "title": "查一下分页",
                "conclusion": "分页改成 cursor",
            }
        }
    )
    out = run("cheese_close_task", {"task": _TASK}, host)
    assert host.requests[0]["body"] == {"conclusion": ""}
    assert "分页改成 cursor" in out


def test_close_task_declares_actual_contributors():
    host = Host()
    run(
        "cheese_close_task",
        {"task": _TASK, "reported_by": "alice", "contributor": ["bob", "carol"]},
        host,
    )
    assert host.requests[0]["body"] == {
        "conclusion": "",
        "reporter_handle": "alice",
        "contributor_handles": ["bob", "carol"],
    }


def _accept_host(**kw):
    return Host(
        {
            ("POST", f"/topics/{_ROOM}/tasks/{_TASK}/accept-card"): {
                "reviewer_handle": "alice",
                "artifact": {"name": "结题报告", "id": "art-1"},
            }
        },
        **kw,
    )


def test_accept_request_pushes_the_work_then_files_the_card():
    host = _accept_host()
    out = run(
        "cheese_accept_request",
        {
            "task": _TASK,
            "reviewer": "alice",
            "reason": "最懂",
            "subject": "fix(accept): require a commit subject",
            "artifact": "art-1",
            "deliver": "报告/结题报告.pdf",
        },
        host,
    )
    assert host.synced == [_TASK]
    [card] = host.requests
    assert card["path"] == f"/topics/{_ROOM}/tasks/{_TASK}/accept-card"
    assert card["body"]["change_subject"] == "fix(accept): require a commit subject"
    assert card["body"]["reviewer_handle"] == "alice"
    assert card["body"]["artifact"] == "art-1"
    assert "已把验收卡递给 alice" in out and "《结题报告》" in out
    assert "报告/结题报告.pdf" in out


def test_accept_request_without_a_subject_touches_nothing():
    """A card that is already filed cannot be un-filed, so the refusal comes
    before the push and before the POST."""
    host = _accept_host()
    with pytest.raises(cheese.PlatformToolError, match="subject"):
        run("cheese_accept_request", {"task": _TASK, "reviewer": "alice"}, host)
    assert host.requests == [] and host.synced == []


def test_accept_request_that_could_not_push_files_no_card():
    """推不上去就不递：一张照着旧提交的卡，比一次失败更糟，因为没人会发现。"""
    host = _accept_host(sync=RuntimeError(cheese_out_of_reach))
    with pytest.raises(RuntimeError, match=cheese_out_of_reach):
        run("cheese_accept_request", {"task": _TASK, "subject": "fix: x"}, host)
    assert host.requests == []


def test_accept_request_without_a_reviewer_lets_the_backend_pick_the_default():
    """「没说」和「说了空的」在后端是两件事：只有前者落到项目默认验收人。"""
    host = _accept_host()
    run(
        "cheese_accept_request",
        {"task": _TASK, "subject": "fix(x): y", "reviewer": ""},
        host,
    )
    assert "reviewer_handle" not in host.requests[0]["body"]


def test_a_deliverable_named_by_its_machine_path_is_sent_task_relative():
    host = _accept_host()
    run(
        "cheese_accept_request",
        {
            "task": _TASK,
            "subject": "docs: 结题报告定稿",
            "artifact": "art-1",
            "deliver": f"/home/u/.cheese/tasks/{_TASK}/报告/结题报告.pdf",
        },
        host,
    )
    assert host.requests[0]["body"]["deliver"] == "报告/结题报告.pdf"


def test_a_new_artifact_hands_back_its_id():
    host = _accept_host()
    out = run(
        "cheese_accept_request",
        {
            "task": _TASK,
            "subject": "feat: add site",
            "new_artifact": "项目官网",
            "about": "对外的产品介绍站",
            "deliver_url": "https://example.test",
        },
        host,
    )
    assert host.requests[0]["body"]["about"] == "对外的产品介绍站"
    assert "art-1" in out


def test_ready_never_pushes_or_files_a_card():
    host = Host(
        {
            ("POST", f"/topics/{_ROOM}/tasks/{_TASK}/ready"): {
                "ready": True,
                "pr_number": 1,
            }
        }
    )
    out = run("cheese_ready", {"task": _TASK}, host)
    assert [p["path"] for p in host.requests] == [
        f"/topics/{_ROOM}/tasks/{_TASK}/ready"
    ]
    assert host.synced == []
    assert "PR #1" in out


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
        run("cheese_lock", {"task": _TASK}, host)
    assert host.requests[0]["body"] == {"kind": "heavy", "task_id": _TASK}


# --- memory, roster, status and the rest -----------------------------------


def test_everyone_outranks_the_private_chats_personal_memory():
    """私聊里的 `everyone` 写的是文档，不是对这一位的个人记忆。"""
    personal = {"CHEESE_MEMORY_SCOPE": "personal", "CHEESE_OWNER": "alice"}
    host = Host(environ=personal)
    assert "项目总览的实况文档" in run(
        "cheese_remember", {"fact": "x", "everyone": True}, host
    )
    assert host.requests[-1]["body"] == {
        "content": "x",
        "topic": _ROOM,
        "scope": "everyone",
    }
    assert "个人记忆" in run("cheese_remember", {"fact": "x"}, host)
    assert host.requests[-1]["body"] == {
        "content": "x",
        "topic": _ROOM,
        "scope": "user",
        "owner": "alice",
    }


def test_an_empty_recall_says_it_is_not_proof_of_absence():
    assert "换个说法" in run("cheese_recall", {"query": "技术栈"}, Host())


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
    assert "Workstation" in out and "文件不会自动迁移" in out and "点头" not in out


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
