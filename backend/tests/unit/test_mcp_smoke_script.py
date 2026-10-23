"""`smoke.py` 是「接进项目之前必须绿」的那条命令，所以它自己先得是对的。

一台 MCP server 的回话有两种：无状态的回 JSON，有状态的回 SSE 流并在
`Mcp-Session-Id` 里给会话号。两种都要走通；会话号必须带回后面的请求，否则真
server 会把 `tools/list` 当成没握过手的陌生人。

假 server 起在本机的临时端口上，脚本按真实 HTTP 打过去——不再另写一份客户端逻辑。
源码 `exec` 进来不经 import：import 会在这份技能目录里落一个 `__pycache__`，而
`test_native_skill_files.py` 要求技能目录里一个多余文件都没有。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.skill_library import _NATIVE_SKILL_SRC

SMOKE = _NATIVE_SKILL_SRC / "mcp-builder" / "scripts" / "smoke.py"


def _smoke() -> dict:
    namespace: dict = {"__name__": "smoke_script", "__file__": str(SMOKE)}
    exec(compile(SMOKE.read_text(encoding="utf-8"), str(SMOKE), "exec"), namespace)
    return namespace


#: 只解一遍：`Failed` 是脚本里定义的类，两次 exec 出来的是两个不同的类，
#: `pytest.raises` 就抓不住。
SMOKE_MODULE = _smoke()


class _Server(ThreadingHTTPServer):
    sse = False
    refused = False
    seen_sessions: list[str | None] = []


def _handler(cls):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            message = json.loads(self.rfile.read(length) or b"{}")
            cls.seen_sessions.append(self.headers.get("Mcp-Session-Id"))
            method = message.get("method")
            if method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if cls.refused:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")
                return
            # 分支写，不用 dict 字面量：字面量会把每一项都先求值一遍，
            # `tools/call` 那项去读 `params["name"]` 就会拖垮 initialize / tools/list。
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "jira_mcp", "version": "0.1.0"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "jira_list_issues",
                            "description": "列出一个项目里的 issue。",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                }
            elif method == "tools/call":
                result = {
                    "content": [
                        {"type": "text", "text": "ok:" + message["params"]["name"]}
                    ],
                    "isError": False,
                }
            else:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(
                {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
            ).encode("utf-8")
            self.send_response(200)
            # 头名一律小写发，跟真 server 一样（uvicorn/hypercorn 发小写）。发成大写
            # 会把「脚本按大小写敏感的 dict.get 读头」这类 bug 掩盖过去——SSE 那一支
            # 曾经就这么漏掉一个。
            self.send_header(
                "content-type", "text/event-stream" if cls.sse else "application/json"
            )
            self.send_header("mcp-session-id", "abc123")
            self.end_headers()
            if cls.sse:
                self.wfile.write(b"event: message\ndata: " + body + b"\n\n")
            else:
                self.wfile.write(body)

    return Handler


@pytest.fixture()
def server():
    cls = type("S", (_Server,), {"seen_sessions": []})
    transport = cls(("127.0.0.1", 0), _handler(cls))
    thread = threading.Thread(target=transport.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{transport.server_address[1]}/mcp", cls
    finally:
        transport.shutdown()
        transport.server_close()
        thread.join(timeout=5)


def test_a_json_server_hands_back_its_tools(server):
    url, _ = server
    outcome = SMOKE_MODULE["run"](url)
    assert outcome["server"] == {"name": "jira_mcp", "version": "0.1.0"}
    assert outcome["protocol"] == "2025-06-18"
    assert [t["name"] for t in outcome["tools"]] == ["jira_list_issues"]


def test_an_sse_server_works_and_the_session_travels_back(server):
    url, cls = server
    cls.sse = True
    outcome = SMOKE_MODULE["run"](url)
    assert outcome["session"] == "abc123"
    assert [t["name"] for t in outcome["tools"]] == ["jira_list_issues"]
    # 通知和 tools/list 都必须带着 initialize 给的那个会话号
    assert cls.seen_sessions[1:] == ["abc123", "abc123"]


def test_a_call_is_made_when_asked(server):
    url, _ = server
    outcome = SMOKE_MODULE["run"](url, call="jira_list_issues", args={"project": "ENG"})
    assert outcome["call"]["content"][0]["text"] == "ok:jira_list_issues"
    assert outcome["call"]["isError"] is False


def test_a_server_that_refuses_says_which_step(server):
    url, cls = server
    cls.refused = True
    with pytest.raises(SMOKE_MODULE["Failed"], match="initialize"):
        SMOKE_MODULE["run"](url)
