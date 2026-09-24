"""平台工具表的契约：会话侧的常量，机器离线时一样都不少（结论 63）。

结论 63（纯平台动作走会话侧 MCP；只有必须在机器上作为进程运行的留在 CLI）、22（其他
一切工具一定在开出来的手上）、23（机器离线：对话、记忆、平台工具可用；项目工具直接标
不可用，不让它们各自超时）。

**这里的「离线」是真的离线**：fixture 里的那台机器要么答 503（「够不着」正是这三个码
之一），要么这条会话根本没有租到手（`unavailable` / `deferred`）。工具表照样答得出每
一样，每一样也照样调得通 —— 它们打的是平台，不是那台机器。只有要机器上一份东西的两
样（读一个文件、推一条任务分支）当场说够不着。这组用例不 mock 任何一层：只要还有一
处向执行器要清单，或者一个纯平台工具绕去了机器，这里就会红。
"""

import base64
import json
import os
import shlex
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.agent.compute_configs import ComputeChoice
from app.domain.agent.executor_transport import MACHINE_OUT_OF_REACH
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution import runtime
from app.domain.agent.market import compute_listings

TASK = "9f8e7d6c-0000-4000-8000-000000000000"

#: 每一样调得通的那一次调用，和它该落在平台的哪个地址上。写死在用例里，不从被测模块
#: 读回来 —— 从表里读一遍再断言它等于自己，删掉一整行也是绿的。
CALLS = {
    "chat_send": ({"content": "这一轮我在这里"}, "POST", "/topics/fixture/messages"),
    "cheese_chat_list": ({}, "GET", "/topics/fixture/history"),
    "cheese_chat_search": ({"query": "口径"}, "GET", "/topics/fixture/history"),
    "cheese_chat_get": (
        {"message_id": "m-1"},
        "GET",
        "/topics/fixture/history/m-1",
    ),
    "cheese_chat_replies": (
        {"message_id": "m-1"},
        "GET",
        "/topics/fixture/history",
    ),
    "cheese_doc_get": ({}, "GET", "/topics/fixture/doc"),
    "cheese_task": ({"title": "数据清洗"}, "POST", "/topics/fixture/split"),
    "cheese_close_task": (
        {"task": TASK},
        "POST",
        f"/topics/fixture/tasks/{TASK}/close",
    ),
    "cheese_describe": (
        {"task": TASK, "subject": "fix: x"},
        "POST",
        f"/topics/fixture/tasks/{TASK}/accept-card/describe",
    ),
    "cheese_ready": ({"task": TASK}, "POST", f"/topics/fixture/tasks/{TASK}/ready"),
    "cheese_tell": (
        {"target": "数据清洗", "message": "口径改了"},
        "POST",
        "/topics/fixture/tell",
    ),
    "cheese_milestone": (
        {"title": "中期汇报"},
        "POST",
        "/projects/fixture-project/milestones",
    ),
    "cheese_title": ({"text": "推荐原型"}, "POST", "/topics/fixture/title"),
    "cheese_decision": ({"text": "用 CF"}, "POST", "/topics/fixture/decision"),
    "cheese_remember": (
        {"fact": "技术栈=FastAPI"},
        "POST",
        "/projects/fixture-project/memory",
    ),
    "cheese_recall": (
        {"query": "技术栈"},
        "POST",
        "/projects/fixture-project/memory/search",
    ),
    "cheese_notify": (
        {"title": "看一眼"},
        "POST",
        "/projects/fixture-project/alerts",
    ),
    "cheese_fetch": ({"url": "https://example.test"}, "POST", "/fetch"),
    "cheese_lock": ({"task": TASK}, "POST", "/topics/fixture/lock"),
    "cheese_unlock": ({"task": TASK}, "POST", "/topics/fixture/unlock"),
    "cheese_members": ({}, "GET", "/topics/fixture/members"),
    "cheese_status": ({}, "GET", "/topics/fixture/status"),
    "cheese_library_ls": ({}, "GET", "/projects/fixture-project/library"),
    "cheese_ask": (
        {"question": "按哪个口径？", "option": ["旧的", "新的"]},
        "POST",
        "/topics/fixture/ask",
    ),
    "cheese_feedback_propose": (
        {
            "title": "工具清单短了一截",
            "kind": "bug",
            "visibility": "public",
            "user_said": "用户没有就这个问题说过话",
        },
        "POST",
        "/topics/fixture/feedback-proposals",
    ),
    "cheese_machine": (
        {"profile": "cloud"},
        "PUT",
        "/topics/fixture/compute-profile",
    ),
    "cheese_note": (
        {"thread": "9f8e7d6c-0000-0000-0000-000000000000", "content": "口径改了"},
        "POST",
        "/topics/fixture/note",
    ),
    "cheese_deliver_at": (
        {"at": "2026-09-21T14:00:00+00:00", "content": "回来看一眼那条 PR"},
        "POST",
        "/topics/fixture/deliveries",
    ),
}

#: 要机器上一份东西的那两样：读一个文件、推一条任务分支。
NEEDS_THE_MACHINE = {
    "cheese_doc_set": ({"path": "notes/doc.md"}, "PUT", "/topics/fixture/doc"),
    "cheese_accept_request": (
        {"task": TASK, "subject": "fix(x): y"},
        "POST",
        f"/topics/fixture/tasks/{TASK}/accept-card",
    ),
}

#: 这条传输自己的四个口子，不是产品动作。
TRANSPORT = {"invoke", "platform_request", "send_user_file", "project_tools"}


def _serve(executor):
    """同一个进程既当平台 API 又当执行器端点：`/execution` 交给 `executor`，别的
    地址是平台，照常答 200。「打的是平台还是那台机器」在用例里是一个可数的事实。"""
    platform_calls = []
    executor_calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def _answer(self, status, body=b""):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length)) if length else {}
            if self.path == "/execution":
                executor_calls.append(payload)
                self._answer(*executor(payload))
                return
            platform_calls.append((self.command, self.path.split("?")[0], payload))
            # 一份每个工具都读得动的回答：有 id、有列表、占到了锁、便条有人接住。
            data = {
                "ok": True,
                "id": "fixture-id",
                "data": [],
                "acquired": True,
                "delivered": True,
                "doc_version": 1,
                **payload,
            }
            self._answer(200, json.dumps({"data": data}).encode())

        do_GET = _serve
        do_POST = _serve
        do_PUT = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, platform_calls, executor_calls


def _session(tmp_path, server, target):
    config = tmp_path / "central.json"
    config.write_text(
        json.dumps({"workspace": str(tmp_path), "central_hooks": {}, **target})
    )
    log = (tmp_path / "central.log").open("w")
    process = runtime.MCPProcess(
        [sys.executable, central.__file__, "transport", str(config)],
        str(tmp_path),
        {
            **os.environ,
            "NO_PROXY": "127.0.0.1",
            "CHEESE_TOKEN": "fixture",
            "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
            "CHEESE_TOPIC": "fixture",
            "CHEESE_PROJECT": "fixture-project",
        },
        log,
    )
    return process, log


@pytest.fixture(params=["answers-503", "unavailable", "deferred"])
def machine_is_gone(tmp_path, request):
    """一个平台在、机器不在的会话，三种不在法。"""
    server, platform_calls, executor_calls = _serve(lambda _: (503, b""))
    port = server.server_port
    target = {
        "answers-503": {"kind": "device", "url": f"http://127.0.0.1:{port}/execution"},
        "unavailable": {"kind": "unavailable"},
        "deferred": {"kind": "deferred"},
    }[request.param]
    process, log = _session(tmp_path, server, target)
    try:
        yield process, platform_calls, executor_calls
    finally:
        process.close()
        server.shutdown()
        server.server_close()
        log.close()


DOC = "# 实况\n\n数据口径定为新版。\n"


@pytest.fixture
def machine_is_here(tmp_path):
    """机器在：它上面有一份文档，推分支的那条命令答成功。"""

    def executor(payload):
        command = payload["params"]["args"]["command"]
        if command.startswith("base64 < "):
            stdout = base64.b64encode(DOC.encode()).decode()
        elif command == "cheese sync --task " + shlex.quote(TASK):
            stdout = ""
        else:
            return 500, b""
        return 200, json.dumps({"value": {"stdout": stdout}}).encode()

    server, platform_calls, executor_calls = _serve(executor)
    process, log = _session(
        tmp_path,
        server,
        {"kind": "device", "url": f"http://127.0.0.1:{server.server_port}/execution"},
    )
    try:
        yield process, platform_calls, executor_calls
    finally:
        process.close()
        server.shutdown()
        server.server_close()
        log.close()


def _listing(process):
    return [tool["name"] for tool in process.call("tools/list", {})["tools"]]


def _tools_by_name(process):
    return {tool["name"]: tool for tool in process.call("tools/list", {})["tools"]}


def _call(process, tool, arguments):
    result = process.call(
        "tools/call",
        {
            "name": tool,
            "arguments": {
                "id": str(uuid.uuid4()),
                "session_id": "fixture",
                **arguments,
            },
        },
    )
    return json.loads(result["content"][0]["text"])


def test_the_tool_table_is_complete_while_the_machine_is_offline(machine_is_gone):
    """机器够不着，表上一样不少 —— 而且这份清单没有向任何人打听过。

    以前这份清单是问执行器要的，所以机器一离线整个 `cheese_*` 家族就从清单里消失，
    agent 被告知 `No such tool available: mcp__native__cheese_status`，正好是它这一
    刻最需要的那句话。
    """
    process, _, executor_calls = machine_is_gone
    assert set(_listing(process)) == {*CALLS, *NEEDS_THE_MACHINE, *TRANSPORT}
    assert executor_calls == [], "列一份工具表不该去问那台机器"


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_each_platform_tool_reaches_the_platform_without_the_machine(
    machine_is_gone, tool
):
    """每一样各一条：机器不在，这一样照样打到平台上，而且一次也没碰那台机器。

    「没碰那台机器」同时就是那条守卫：这些工具的调用路径上没有一次
    `client.call("invoke", …)` —— 有的话，执行器那一侧会数到一次 503，或者这次调用
    会被答成「够不着」。
    """
    process, platform_calls, executor_calls = machine_is_gone
    arguments, method, path = CALLS[tool]

    outcome = _call(process, tool, arguments)

    assert "deny" not in outcome, outcome
    assert (method, path) in [(m, p) for m, p, _ in platform_calls], platform_calls
    assert executor_calls == [], f"{tool} 经过了那台机器"


@pytest.mark.parametrize("tool", sorted(NEEDS_THE_MACHINE))
def test_a_tool_that_needs_a_file_from_the_machine_says_it_is_out_of_reach(
    machine_is_gone, tool
):
    """读文件、推分支要那台机器：它不在，当场说不在，而且什么都没写到平台上。

    写上去的话，递出去的是一张照着旧提交的卡，或者一份空文档 —— 没人会发现。
    """
    process, platform_calls, _ = machine_is_gone
    arguments, method, path = NEEDS_THE_MACHINE[tool]

    outcome = _call(process, tool, arguments)

    assert outcome == {"deny": MACHINE_OUT_OF_REACH}
    assert (method, path) not in [(m, p) for m, p, _ in platform_calls]


def test_the_living_doc_is_read_off_the_machine(machine_is_here):
    process, platform_calls, executor_calls = machine_is_here

    outcome = _call(process, "cheese_doc_set", {"path": "notes/doc.md"})

    assert "deny" not in outcome, outcome
    [put] = [
        body
        for m, p, body in platform_calls
        if (m, p) == ("PUT", "/topics/fixture/doc")
    ]
    assert put["content"] == DOC
    # 没读过就写，出示的是 0 —— 只有还没有文档时平台才收。
    assert put["expected_version"] == 0
    # 相对路径锚在机器的工作区上，不是会话这一侧的哪个目录。
    [read] = executor_calls
    assert read["params"]["args"]["command"].endswith("/notes/doc.md")


def test_a_write_after_a_read_carries_the_version_it_read(machine_is_here):
    process, platform_calls, _ = machine_is_here
    _call(process, "cheese_doc_get", {})
    _call(process, "cheese_doc_set", {"path": "notes/doc.md"})
    [put] = [body for m, p, body in platform_calls if m == "PUT"]
    assert put["expected_version"] == 1


def test_acceptance_pushes_the_work_before_it_files_the_card(machine_is_here):
    process, platform_calls, executor_calls = machine_is_here

    outcome = _call(
        process,
        "cheese_accept_request",
        {"task": TASK, "subject": "fix(x): y", "reviewer": "lisi"},
    )

    assert "deny" not in outcome, outcome
    assert "已把验收卡递给 lisi" in outcome["result"]["stdout"]
    [sync] = executor_calls
    assert sync["params"]["args"]["command"] == f"cheese sync --task {TASK}"
    [card] = [
        body
        for m, p, body in platform_calls
        if p == f"/topics/fixture/tasks/{TASK}/accept-card"
    ]
    assert card["change_subject"] == "fix(x): y"
    assert card["reviewer_handle"] == "lisi"


def test_a_project_tool_says_the_machine_is_gone_instead_of_waiting(machine_is_gone):
    """项目工具如实标不可用，而且只问一次（结论 22、23）。

    一整轮里 agent 会一个接一个地读文件、跑命令。每一个都去撞一次执行器连接的读超
    时，这一轮就这么耗光了 —— 而机器够不着这件事，第一次就已经问清楚了。
    """
    process, _, executor_calls = machine_is_gone

    def read_a_file():
        return process.call(
            "tools/call",
            {
                "name": "invoke",
                "arguments": {
                    "id": str(uuid.uuid4()),
                    "session_id": "fixture",
                    "tool": "Read",
                    "args": {"file_path": "/work/README.md"},
                },
            },
        )

    # 第一次是去问的那一次 —— 机器够不着，只有问过才知道。
    with pytest.raises(RuntimeError, match=MACHINE_OUT_OF_REACH):
        read_a_file()

    for _ in range(2):
        outcome = json.loads(read_a_file()["content"][0]["text"])
        assert outcome["deny"] == MACHINE_OUT_OF_REACH, outcome
    assert len(executor_calls) <= 1, f"后面几次又去撞了一遍：{executor_calls}"


def test_the_feedback_tool_says_when_to_use_it(machine_is_gone):
    """这个工具**唯一的说明就是那段文字**，所以触发时机必须出现在模型读到的工具说明
    里——四条触发时机、「什么时候不该提」、以及它是提案所以不打断。"""
    process, _, _ = machine_is_gone
    description = _tools_by_name(process)["cheese_feedback_propose"]["description"]

    for trigger in (
        "某个工具或命令反复失败",
        "你做不到用户要求的事",
        "用户指出你的错,或者你自己发现犯了错",
        "用户让你提",
    ):
        assert trigger in description
    assert "什么时候不该提" in description
    assert "用户的使用方式" in description
    assert "中途" in description


def test_cheese_machine_declares_exactly_the_profiles_the_backend_accepts(
    machine_is_gone,
):
    """`cheese_machine` 的 schema 是 agent 手上唯一的契约：`profile` 能填什么，要写在
    `enum` 里，而且要和后端的算力目录是同一组。

    以前它只写了「算力档位名」，agent 连猜 default、standard、small 几次，每次拿回
    422，最后去问人「档位名是什么」。
    """
    process, _, _ = machine_is_gone
    profile = _tools_by_name(process)["cheese_machine"]["inputSchema"]["properties"][
        "profile"
    ]
    declared = profile["enum"]

    catalog = {
        pool.id
        for pool in compute_listings(
            SimpleNamespace(microcloud_base_url="", microcloud_tenant_secret=""),
            device_online=True,
        )
    }
    assert set(declared) == catalog

    for value in declared:
        ComputeChoice.model_validate({"name": "契约", "profile": value})
    for guess in ("default", "standard", "cheese-box", "small", "medium"):
        assert guess not in declared
        with pytest.raises(ValidationError):
            ComputeChoice.model_validate({"name": "契约", "profile": guess})
