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

import json
import os
import shlex
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.agent.executor_transport import MACHINE_OUT_OF_REACH
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution import mcp_process
from tests.support.session_runner import standing_runner

TASK = "9f8e7d6c-0000-4000-8000-000000000000"
#: The conversation these sessions run in (CHEESE_TOPIC): a task's own
#: session, so the task tools act on it.
TOPIC = TASK

#: 每一样调得通的那一次调用，和它该落在平台的哪个地址上。写死在用例里，不从被测模块
#: 读回来 —— 从表里读一遍再断言它等于自己，删掉一整行也是绿的。
CALLS = {
    "chat_send": ({"content": "这一轮我在这里"}, "POST", f"/topics/{TOPIC}/messages"),
    "cheese_chat.edit": (
        {"action": "edit", "message_id": "m-1", "content": "改过的这一句"},
        "PATCH",
        "/blocks/m-1",
    ),
    "todo_write": (
        {"todos": [{"content": "读现有实现", "status": "in_progress"}]},
        "PUT",
        f"/topics/{TOPIC}/progress",
    ),
    "cheese_chat.list": ({"action": "list"}, "GET", f"/topics/{TOPIC}/history"),
    "cheese_chat.search": (
        {"action": "search", "query": "口径"},
        "GET",
        f"/topics/{TOPIC}/history",
    ),
    "cheese_chat.get": (
        {"action": "get", "message_id": "m-1"},
        "GET",
        f"/topics/{TOPIC}/history/m-1",
    ),
    "cheese_chat.replies": (
        {"action": "replies", "message_id": "m-1"},
        "GET",
        f"/topics/{TOPIC}/history",
    ),
    # The task's document: the fixture platform names every id "fixture-id".
    "cheese_doc.get": ({"action": "get"}, "GET", "/documents/fixture-id"),
    # 不碰机器：任务开始前改动留不下，起草实况文档正是那时要做的事。
    "cheese_doc.set": (
        {"action": "set", "content": "# 实况\n"},
        "PUT",
        "/documents/fixture-id",
    ),
    "cheese_doc.edit": (
        {"action": "edit", "edits": [{"old": "第一段", "new": "第一段，改过"}]},
        "POST",
        "/documents/fixture-id/edits",
    ),
    "cheese_doc.new": (
        {"action": "new", "title": "竞品定价对比"},
        "POST",
        "/projects/fixture-project/documents",
    ),
    "cheese_doc.list": (
        {"action": "list"},
        "GET",
        "/projects/fixture-project/documents",
    ),
    # 小程序跟着文档走：新建落在这份文档下，改和读按它的短号。
    "cheese_app.save": (
        {"action": "save", "code": "<div>试算</div>", "title": "试算"},
        "POST",
        "/documents/fixture-id/apps",
    ),
    "cheese_app.get": ({"action": "get", "app": "q7m2kx4t"}, "GET", "/apps/q7m2kx4t"),
    "cheese_canvas.get": (
        {"action": "get", "canvas": "c-1"},
        "GET",
        "/canvases/c-1/text",
    ),
    "cheese_canvas.edit": (
        {
            "action": "edit",
            "canvas": "c-1",
            "changes": [{"op": "add", "content": "目标"}],
        },
        "POST",
        "/canvases/c-1/changes",
    ),
    # One of an object's verbs is keyed `<tool>.<action>`, and its call
    # carries that action.
    "cheese_task.open": (
        {"action": "open", "title": "数据清洗", "brief": "按新口径重算"},
        "POST",
        f"/topics/{TOPIC}/teammate-tasks",
    ),
    "cheese_task.close": ({"action": "close"}, "POST", f"/topics/{TOPIC}/close"),
    "cheese_task.reopen": ({"action": "reopen"}, "POST", f"/topics/{TOPIC}/reopen"),
    "cheese_task.state": (
        {"action": "state", "state": "waiting", "note": "等 CI"},
        "PUT",
        f"/topics/{TOPIC}/task/progress",
    ),
    "cheese_card.describe": (
        {"action": "describe", "subject": "fix: x"},
        "POST",
        f"/topics/{TOPIC}/accept-card/describe",
    ),
    "cheese_card.redeliver": (
        {"action": "redeliver", "deliver_url": "https://site.example/r"},
        "POST",
        f"/topics/{TOPIC}/accept-card/redeliver",
    ),
    "cheese_card.ready": ({"action": "ready"}, "POST", f"/topics/{TOPIC}/ready"),
    "cheese_room.rename": (
        {"action": "rename", "text": "推荐原型"},
        "POST",
        f"/topics/{TOPIC}/title",
    ),
    "cheese_notify": (
        {"title": "看一眼"},
        "POST",
        "/projects/fixture-project/alerts",
    ),
    "cheese_web.fetch": (
        {"action": "fetch", "url": "https://example.test"},
        "POST",
        "/fetch",
    ),
    "cheese_web.search": ({"action": "search", "query": "芝士"}, "GET", "/web-search"),
    "cheese_help": ({"query": "验收"}, "POST", "/docs/agent/search"),
    "cheese_help.page": ({"page": "accept"}, "POST", "/docs/agent/read"),
    "cheese_room.members": ({"action": "members"}, "GET", f"/topics/{TOPIC}/members"),
    "cheese_session.status": ({"action": "status"}, "GET", f"/topics/{TOPIC}/status"),
    "cheese_library.list": (
        {"action": "list"},
        "GET",
        "/projects/fixture-project/library",
    ),
    "cheese_ask": (
        {
            "questions": [
                {
                    "question": "按哪个口径？",
                    "options": [{"text": "旧的"}, {"text": "新的"}],
                }
            ]
        },
        "POST",
        f"/topics/{TOPIC}/asks",
    ),
    "cheese_feedback.propose": (
        {
            "action": "propose",
            "title": "工具清单短了一截",
            "kind": "bug",
            "visibility": "public",
            "user_said_absent": True,
        },
        "POST",
        f"/topics/{TOPIC}/feedback-proposals",
    ),
    "cheese_feedback.list": ({"action": "list", "query": "保存"}, "GET", "/feedback"),
    "cheese_feedback.get": (
        {"action": "get", "feedback": "FB-12"},
        "GET",
        "/feedback/FB-12",
    ),
    "cheese_feedback.claim": (
        {"action": "claim", "feedback": "FB-12"},
        "POST",
        "/feedback/FB-12/claim",
    ),
    "cheese_feedback.release": (
        {"action": "release", "feedback": "FB-12"},
        "DELETE",
        "/feedback/FB-12/claim",
    ),
    "cheese_run_records": ({"group": "errors"}, "GET", "/run-records"),
    "cheese_session.environment": (
        {"action": "environment"},
        "GET",
        f"/topics/{TOPIC}/environment",
    ),
    "cheese_session.note": (
        {
            "action": "note",
            "thread": "9f8e7d6c-0000-0000-0000-000000000000",
            "content": "口径改了",
        },
        "POST",
        f"/topics/{TOPIC}/note",
    ),
    "cheese_session.deliver_at": (
        {
            "action": "deliver_at",
            "at": "2026-09-21T14:00:00+00:00",
            "content": "回来看一眼那条 PR",
        },
        "POST",
        f"/topics/{TOPIC}/deliveries",
    ),
    "cheese_routine.create": (
        {
            "action": "create",
            "title": "每周整理一次进展",
            "instructions": "把这一周的进展整理成一页",
            "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
            "owner_handle": "alice",
        },
        "POST",
        f"/topics/{TOPIC}/routines",
    ),
    "cheese_routine.list": (
        {"action": "list"},
        "GET",
        "/projects/fixture-project/routines",
    ),
    "cheese_routine.update": (
        {"action": "update", "routine": "r-1", "timezone": "UTC"},
        "PATCH",
        "/routines/r-1",
    ),
    "cheese_routine.pause": (
        {"action": "pause", "routine": "r-1"},
        "POST",
        "/routines/r-1/pause",
    ),
    "cheese_routine.report": (
        {"action": "report", "run": "run-1", "status": "failed", "summary": "接口挂了"},
        "POST",
        "/routine-runs/run-1/report",
    ),
    "cheese_skill.propose": (
        {
            "action": "propose",
            "name": "weekly-report",
            "title": "周报",
            "description": "用户要这周的周报时",
            "body": "先写变坏的指标",
            "taught": ["先说坏消息"],
            "accepted": "用户说就这样",
        },
        "POST",
        f"/topics/{TOPIC}/skills",
    ),
    "cheese_skill.update": (
        {
            "action": "update",
            "skill": "m-1",
            "body": "先写变坏的指标",
            "reason": "顺序反了",
        },
        "PATCH",
        "/skills/m-1",
    ),
    "cheese_skill.search": (
        {"action": "search", "query": "周报"},
        "POST",
        "/agent/skills/search",
    ),
    "cheese_skill.read": (
        {"action": "read", "name": "weekly-report"},
        "POST",
        "/agent/skills/read",
    ),
    "platform_request": (
        {
            "method": "PUT",
            "path": f"/topics/{TOPIC}/members/bob",
            "body": {"role": "admin"},
        },
        "PUT",
        f"/topics/{TOPIC}/members/bob",
    ),
}

#: 要机器的那一样：推一条任务分支。
NEEDS_THE_MACHINE = {
    "cheese_card.request": (
        {"action": "request", "subject": "fix(x): y", "completes_task": True},
        "POST",
        f"/topics/{TOPIC}/accept-card",
    ),
}

#: 只等那台机器、什么也不写到平台上的那一样。
WAITS_FOR_THE_MACHINE = {"cheese_session.wait"}

#: 这条传输自己的三个口子，不是产品动作。
TRANSPORT = {
    "invoke",
    "permission",
    "send_user_file",
}


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
            # 一份每个工具都读得动的回答：有 id、有列表、占到了锁、便条有人接住、
            # 评论线程读得到版本。
            data = {
                "ok": True,
                "id": "fixture-id",
                "data": [],
                "acquired": True,
                "delivered": True,
                "doc_version": 1,
                "revision": 1,
                **payload,
            }
            if self.path == f"/topics/{TOPIC}/asks":
                data = {"blocks": [{"id": "fixture-id"}], "request_id": "fixture"}
            self._answer(200, json.dumps({"data": data}).encode())

        do_GET = _serve
        do_POST = _serve
        do_PUT = _serve
        do_PATCH = _serve
        do_DELETE = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, platform_calls, executor_calls


def _session(tmp_path, server, target, extra_env=None, *, runner_socket):
    config = tmp_path / "central.json"
    config.write_text(
        json.dumps({"workspace": str(tmp_path), "central_hooks": {}, **target})
    )
    log = (tmp_path / "central.log").open("w")
    process = mcp_process.MCPProcess(
        [sys.executable, central.__file__, "transport", str(config)],
        str(tmp_path),
        {
            **os.environ,
            "NO_PROXY": "127.0.0.1",
            "CHEESE_TOKEN": "fixture",
            "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
            "CHEESE_TOPIC": TOPIC,
            "CHEESE_PROJECT": "fixture-project",
            # The session's runner, which names the run each platform call
            # is made in (`executor_transport.PlatformHost.run`).
            "CHEESE_SESSION_SOCKET": runner_socket,
            **(extra_env or {}),
        },
        log,
    )
    return process, log


@pytest.fixture
def runner_socket():
    """The session's runner: it names the run every platform call is made in."""
    with standing_runner("claude-code/1/1") as path:
        yield path


@pytest.fixture(params=["answers-503", "unavailable", "deferred"])
def machine_is_gone(tmp_path, request, runner_socket):
    """一个平台在、机器不在的会话，三种不在法。"""
    server, platform_calls, executor_calls = _serve(lambda _: (503, b""))
    port = server.server_port
    target = {
        "answers-503": {"kind": "device", "url": f"http://127.0.0.1:{port}/execution"},
        "unavailable": {"kind": "unavailable"},
        "deferred": {"kind": "deferred"},
    }[request.param]
    process, log = _session(tmp_path, server, target, runner_socket=runner_socket)
    try:
        yield process, platform_calls, executor_calls
    finally:
        process.close()
        server.shutdown()
        server.server_close()
        log.close()


DOC = "# 实况\n\n数据口径定为新版。\n"


@pytest.fixture
def machine_is_here(tmp_path, runner_socket):
    """机器在：推分支的那条命令和等机器的空命令答成功。"""

    def executor(payload):
        command = payload["params"]["args"]["command"]
        if command not in ("cheese sync --task " + shlex.quote(TOPIC), "true"):
            return 500, b""
        return 200, json.dumps({"value": {"stdout": ""}}).encode()

    server, platform_calls, executor_calls = _serve(executor)
    process, log = _session(
        tmp_path,
        server,
        {"kind": "device", "url": f"http://127.0.0.1:{server.server_port}/execution"},
        runner_socket=runner_socket,
    )
    try:
        yield process, platform_calls, executor_calls
    finally:
        process.close()
        server.shutdown()
        server.server_close()
        log.close()


def _tool(key):
    """The tool a key names: `cheese_task.close` is a call to `cheese_task`."""
    return key.partition(".")[0]


def _verbs(process):
    """Each tool on the listing, and each verb of an object by its key."""
    keys = set()
    for tool in process.call("tools/list", {})["tools"]:
        verbs = tool["inputSchema"].get("properties", {}).get("action", {}).get("enum")
        keys |= {f"{tool['name']}.{v}" for v in verbs} if verbs else {tool["name"]}
    return keys


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
    assert set(_listing(process)) == {
        _tool(key)
        for key in (*CALLS, *NEEDS_THE_MACHINE, *WAITS_FOR_THE_MACHINE, *TRANSPORT)
    }
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

    outcome = _call(process, _tool(tool), arguments)

    assert "deny" not in outcome, outcome
    assert (method, path) in [(m, p) for m, p, _ in platform_calls], platform_calls
    assert executor_calls == [], f"{tool} 经过了那台机器"


def test_a_named_document_is_read_and_changed_instead_of_the_rooms(
    machine_is_gone,
):
    """点了名的文档（项目资料库里的那一份）就读写那一份，不去碰话题的实况文档。"""
    process, platform_calls, _ = machine_is_gone

    _call(process, "cheese_doc", {"action": "get", "document": "library-doc"})
    _call(
        process,
        "cheese_doc",
        {
            "action": "edit",
            "document": "library-doc",
            "edits": [{"old": "旧", "new": "新"}],
        },
    )

    reached = [(m, p) for m, p, _ in platform_calls]
    assert ("GET", "/documents/library-doc") in reached
    assert ("POST", "/documents/library-doc/edits") in reached
    assert ("GET", f"/topics/{TOPIC}/document") not in reached


@pytest.mark.parametrize(
    ("arguments", "said"),
    [
        (
            {
                "title": "每周整理一次进展",
                "instructions": "整理",
                "spec": {"freq": "weekly", "time": "09:00"},
            },
            "每周执行要给出星期几",
        ),
        (
            {"title": "整点跑", "instructions": "整理", "spec": {"freq": "daily"}},
            "执行时间要写成 HH:MM",
        ),
        (
            {"title": "整点跑", "instructions": "整理", "timezone": "Mars/Olympus"},
            "不认识的时区",
        ),
        (
            {"title": "整点跑", "instructions": "整理", "trigger": "clock"},
            "trigger 只能是",
        ),
    ],
)
def test_a_routine_the_backend_would_refuse_is_refused_before_it_is_sent(
    machine_is_gone, arguments, said
):
    """定时规则的取值错在发出之前说出来。

    这些错后端也会拒（`routine/schedule.py` 的 `normalize`），但那时 agent 已经等了
    一个来回，拿回来的只有一句「频率只能是…」，而它本来就知道时间该写成什么样。拒
    在这里，而且一个请求都没发出去 —— 发出去的那一次会起草出一条谁都不要的规则。
    """
    process, platform_calls, executor_calls = machine_is_gone

    outcome = _call(
        process,
        "cheese_routine",
        {"action": "create", **arguments, "owner_handle": "alice"},
    )

    assert said in outcome["deny"], outcome
    assert platform_calls == [], platform_calls
    assert executor_calls == []


@pytest.mark.parametrize(
    ("tool", "arguments", "said"),
    [
        (
            "cheese_task",
            {"title": "数据清洗"},
            "cheese_task 要 action：open / state / close / reopen",
        ),
        (
            "cheese_task",
            {"action": "state"},
            "cheese_task(action=state) 缺少参数：state",
        ),
        (
            "cheese_task",
            {"action": "close", "conclusion": "做完了", "subject": "fix: x"},
            "cheese_task(action=close) 不收 subject：subject 是 "
            "cheese_card(action=request)、cheese_card(action=describe) 的参数。",
        ),
        (
            "cheese_card",
            {"action": "request", "completes_task": True},
            "cheese_card(action=request) 缺少参数：subject",
        ),
        (
            "cheese_card",
            {"action": "ready", "deliver_url": "https://site.example/r"},
            "cheese_card(action=ready) 不收 deliver_url：deliver_url 是 "
            "cheese_card(action=request)、cheese_card(action=redeliver) 的参数。",
        ),
        (
            "cheese_chat",
            {"action": "edit", "message_id": "m-1"},
            "cheese_chat(action=edit) 缺少参数：content",
        ),
        (
            "cheese_doc",
            {"action": "get", "query": "范围"},
            "cheese_doc(action=get) 不收 query：query 是 cheese_chat(action=search)、",
        ),
        # Making a channel or changing who is in one is a person's 芝士's, as
        # the person: the room's own room tool has neither.
        (
            "cheese_room",
            {"action": "create", "where": "p", "title": "新频道"},
            "cheese_room 要 action：members / rename",
        ),
        # Deciding a card is a person's 芝士's, as the reviewer: the room's card
        # has no such verb.
        (
            "cheese_card",
            {"action": "decide", "ref": "p/tasks/1/review", "choice": "accept"},
            "cheese_card 要 action：request / describe / redeliver / ready",
        ),
    ],
)
def test_a_verb_is_refused_what_it_does_not_take_before_anything_is_sent(
    machine_is_gone, tool, arguments, said
):
    """A call its verb cannot take says what to pass instead, and nothing
    reaches the platform: a task closed with a card's title, or a card filed
    with no title, is not half done — it is not done."""
    process, platform_calls, executor_calls = machine_is_gone

    outcome = _call(process, tool, arguments)

    assert said in outcome["deny"], outcome
    assert platform_calls == [], platform_calls
    assert executor_calls == []


@pytest.mark.parametrize("tool", sorted(NEEDS_THE_MACHINE))
def test_a_tool_that_needs_the_machine_says_it_is_out_of_reach(machine_is_gone, tool):
    """推分支要那台机器：它不在，当场说不在，而且什么都没写到平台上。

    写上去的话，递出去的是一张照着旧提交的卡 —— 没人会发现。
    """
    process, platform_calls, _ = machine_is_gone
    arguments, method, path = NEEDS_THE_MACHINE[tool]

    outcome = _call(process, _tool(tool), arguments)

    assert outcome == {"deny": MACHINE_OUT_OF_REACH}
    assert (method, path) not in [(m, p) for m, p, _ in platform_calls]


def test_waiting_for_a_machine_that_is_gone_says_so_at_once(machine_is_gone):
    """等一台够不着的机器，当场说够不着，不把 agent 晾到超时。"""
    process, platform_calls, _ = machine_is_gone

    outcome = _call(process, "cheese_session", {"action": "wait"})

    assert outcome == {"deny": MACHINE_OUT_OF_REACH}
    assert platform_calls == []


def test_waiting_for_a_machine_that_is_here_returns_ready(machine_is_here):
    """机器在：等它就是在它上面跑一条什么也不做的命令，跑通就是就绪。"""
    process, _, executor_calls = machine_is_here

    outcome = _call(process, "cheese_session", {"action": "wait"})

    assert "deny" not in outcome, outcome
    assert "执行环境已就绪" in json.dumps(outcome, ensure_ascii=False)
    assert [c["params"]["args"]["command"] for c in executor_calls] == ["true"]


def test_a_write_after_a_read_carries_the_version_it_read(machine_is_here):
    process, platform_calls, _ = machine_is_here
    _call(process, "cheese_doc", {"action": "get"})
    _call(process, "cheese_doc", {"action": "set", "content": DOC})
    [put] = [body for m, p, body in platform_calls if m == "PUT"]
    assert put["expected_version"] == 1


def test_acceptance_pushes_the_work_before_it_files_the_card(machine_is_here):
    process, platform_calls, executor_calls = machine_is_here

    outcome = _call(
        process,
        "cheese_card",
        {
            "action": "request",
            "subject": "fix(x): y",
            "reviewer": "lisi",
            "completes_task": True,
        },
    )

    assert "deny" not in outcome, outcome
    assert "已把验收卡递给 lisi" in outcome["result"]["stdout"]
    [sync] = executor_calls
    assert sync["params"]["args"]["command"] == f"cheese sync --task {TOPIC}"
    [card] = [
        body for m, p, body in platform_calls if p == f"/topics/{TOPIC}/accept-card"
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
    description = _tools_by_name(process)["cheese_feedback"]["description"]

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


def test_the_task_state_tool_offers_exactly_the_states_the_platform_takes():
    """The tool's file reaches the machine on its own, so its states are a copy;
    a state the backend refuses, or one it takes that the tool never offers,
    shows up here instead of as a 422 in someone's task."""
    from app.domain.agent.harness.codex.tools import platform_tools
    from app.domain.room_task.models import TaskProgress
    from app.domain.room_task.progress import NOTE_MAX_CHARS

    tools = platform_tools()
    state = tools.PLATFORM_TOOLS.actions("cheese_task")["state"]
    offered = state["properties"]["state"]["enum"]
    assert set(offered) == {state.value for state in TaskProgress}
    assert tools.TASK_PROGRESS_NOTE_CHARS == NOTE_MAX_CHARS


def test_the_switch_decides_whether_the_tool_is_on_the_listing(tmp_path, runner_socket):
    """`cheese_session(action=compact)` 默认关着，这一条钉的是**清单**：会话侧那份 `tools/list`
    里没有它，agent 连它的名字都看不到；部署在会话的环境里写下那一句，它才在清单
    上。表本身两种情况下都完整（上面那条用例读的就是它）。"""
    server, _, _ = _serve(lambda _: (503, b""))
    try:
        target = {"kind": "unavailable"}
        (tmp_path / "off").mkdir()
        (tmp_path / "on").mkdir()
        off, log = _session(
            tmp_path / "off", server, target, runner_socket=runner_socket
        )
        on, log_on = _session(
            tmp_path / "on",
            server,
            target,
            {"CHEESE_COMPACT_TOOL": "1"},
            runner_socket=runner_socket,
        )
        try:
            # 开关动的是 cheese_session 的一个动词：关着时它的 action 里没有 compact。
            assert _verbs(off) == _verbs(on) - {"cheese_session.compact"}
            assert "cheese_session.compact" in _verbs(on)
        finally:
            off.close()
            on.close()
            log.close()
            log_on.close()
    finally:
        server.shutdown()
        server.server_close()
