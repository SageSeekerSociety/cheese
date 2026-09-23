"""平台工具表的契约：六样，会话侧，机器离线时一样都不少。

结论 21（平台的 MCP 永远在 claude 进程所在的机器上，六样）、22（其他一切工具一定在
开出来的手上）、23（机器离线：项目工具直接标不可用，不让它们各自超时）。

**这里的「离线」是真的离线**：整个 fixture 里没有执行器，那个地址上答 503 的是一台
「够不着」的机器。工具表照样答得出六样，六样也照样调得通 —— 它们打的是平台，不是
那台机器。这就是这一组用例要证的东西，也是它为什么不 mock 任何一层：只要还有一处向
执行器要清单，这里就会红。
"""

import argparse
import importlib.util
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.agent.executor_transport import MACHINE_OUT_OF_REACH
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution import runtime

#: 平台的 MCP 上的全部（结论 21）。写死在用例里，不从被测模块读回来 —— 从表里读一
#: 遍再断言它等于自己，删掉一整行也是绿的。
SIX = (
    "chat_send",
    "cheese_ask",
    "cheese_feedback_propose",
    "cheese_machine",
    "cheese_note",
    "cheese_deliver_at",
)

CLI = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"

#: 每一样调得通的那一次调用，和它该落在平台的哪个地址上。
CALLS = {
    "chat_send": ({"content": "这一轮我在这里"}, "POST", "/topics/fixture/messages"),
    "cheese_ask": (
        {"question": "按哪个口径？", "option": ["旧的", "新的"]},
        "POST",
        "/topics/fixture/ask",
    ),
    "cheese_feedback_propose": (
        {
            "title": "工具清单短了一截",
            "kind": "bug",
            "visibility": "team",
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


@pytest.fixture
def machine_is_gone(tmp_path):
    """一个平台在、机器不在的会话。

    同一个进程既当平台 API 又当执行器端点：`/execution` 一律答 503（「够不着」正是
    这三个码之一），别的地址是平台，照常答 200。这样「打的是平台还是那台机器」在用
    例里是一个可数的事实，不是一句话。
    """
    platform_calls = []
    executor_calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length)) if length else {}
            if self.path == "/execution":
                executor_calls.append(payload)
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            platform_calls.append((self.command, self.path, payload))
            data = json.dumps({"data": {"ok": True, **payload}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = _serve
        do_PUT = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    config = tmp_path / "central.json"
    config.write_text(
        json.dumps(
            {
                "kind": "device",
                "url": f"http://127.0.0.1:{port}/execution",
                "workspace": str(tmp_path),
                "central_hooks": {},
            }
        )
    )
    log = (tmp_path / "central.log").open("w")
    process = runtime.MCPProcess(
        [sys.executable, central.__file__, "transport", str(config)],
        str(tmp_path),
        {
            **os.environ,
            "NO_PROXY": "127.0.0.1",
            "CHEESE_TOKEN": "fixture",
            "CHEESE_API": f"http://127.0.0.1:{port}",
            "CHEESE_TOPIC": "fixture",
            "CHEESE_PROJECT": "fixture",
        },
        log,
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


def _cli_subparser(parser, name):
    group = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return group.choices[name]


def test_the_tool_table_is_complete_while_the_machine_is_offline(machine_is_gone):
    """机器够不着，六样一个不少 —— 而且这份清单没有向任何人打听过。

    以前这份清单是问执行器要的，所以机器一离线整个 `cheese_*` 家族就从清单里消失，
    agent 被告知 `No such tool available: mcp__native__cheese_status`，正好是它这一
    刻最需要的那句话。
    """
    process, _, executor_calls = machine_is_gone
    names = _listing(process)

    for tool in SIX:
        assert tool in names, f"{tool} 不在工具表上：{names}"
    assert executor_calls == [], "列一份工具表不该去问那台机器"


def test_the_table_is_exactly_the_six_plus_the_three_the_transport_owns(
    machine_is_gone,
):
    """表上只有六样（结论 21）。

    另外三个不是产品动作，是这条传输自己的三个口子：`invoke` 是项目工具（文件、命
    令）过河的那一程，`platform_request` 是没有对应工具时的原始 API 入口，
    `send_user_file` 是 SendUserFile 把文件递进本房间的那一程。
    """
    process, _, _ = machine_is_gone
    assert set(_listing(process)) == {
        *SIX,
        "invoke",
        "platform_request",
        "send_user_file",
    }


def test_feedback_propose_description_carries_the_cli_guidance(machine_is_gone):
    """`cheese_feedback_propose` 的 description 必须是父命令 `cheese feedback -h` 的文案
    **加上** 叶子 `cheese feedback propose -h` 的文案，两段都少不得。

    只抄叶子命令的文案（「落一张提案卡……」）就是这次的 bug：四条触发和
    「什么时候不该提」写在父命令上，agent 在 MCP 这一侧根本看不见，于是它不知道
    什么时候该用这个工具。这里同时钉住两件事：文案内容，以及它和 argparse 树
    线程出来的那一份一字不差。
    """
    process, _, _ = machine_is_gone
    description = _tools_by_name(process)["cheese_feedback_propose"]["description"]

    loader = SourceFileLoader("cheese_cli_contract", str(CLI))
    spec = importlib.util.spec_from_loader("cheese_cli_contract", loader)
    assert spec is not None
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    feedback = _cli_subparser(cli.build_parser(), "feedback")
    propose = _cli_subparser(feedback, "propose")
    assert description == feedback.description + "\n\n" + propose.description

    for trigger in (
        "某个工具或命令反复失败",
        "你做不到用户要求的事",
        "用户指出你的错,或者你自己发现犯了错",
        "用户让你提",
    ):
        assert trigger in description
    assert "什么时候不该提" in description
    assert description.index("什么时候该提") < description.index(
        "把你发现的问题提成一张提案卡"
    )


@pytest.mark.parametrize("tool", SIX)
def test_each_platform_tool_reaches_the_platform_without_the_machine(
    machine_is_gone, tool
):
    """六样各一条：机器不在，这一样照样打到平台上，而且一次也没碰那台机器。

    「没碰那台机器」同时就是那条守卫：这六样的调用路径上没有一次
    `client.call("invoke", …)` —— 有的话，执行器那一侧会数到一次 503。
    """
    process, platform_calls, executor_calls = machine_is_gone
    arguments, method, path = CALLS[tool]

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

    outcome = json.loads(result["content"][0]["text"])
    assert "deny" not in outcome, outcome
    assert (method, path) in [(m, p) for m, p, _ in platform_calls], platform_calls
    assert executor_calls == [], f"{tool} 经过了那台机器"


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
    assert len(executor_calls) == 1, f"后面几次又去撞了一遍：{executor_calls}"
