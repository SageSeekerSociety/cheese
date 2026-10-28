#!/usr/bin/env python3
"""对一台 MCP server 走一遍握手，看它到底挂了哪些工具。

纯标准库，不装 SDK：这个脚本要在用户机器上直接跑起来，装不上包也得能用。
用法：

    python3 smoke.py https://mcp.example.com/jira
    python3 smoke.py https://mcp.example.com/jira --header 'Authorization: Bearer …'
    python3 smoke.py https://mcp.example.com/jira --call jira_list_issues \
      --args '{"project":"ENG"}'

握手 = initialize → notifications/initialized → tools/list。任何一步不是 2xx、
或回里没有 result，就是这台 server 还没接好；接进项目之前这条必须绿。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

#: 同时接受两种返回：无状态 server 回 JSON，有状态的回 SSE 流。
ACCEPT = "application/json, text/event-stream"
PROTOCOL = "2025-06-18"


class Failed(Exception):
    """这台 server 没按 MCP 回话。消息直接给人看，所以写清是哪一步、回了什么。"""


def _header(headers: dict, name: str) -> str | None:
    lower = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lower), None)


def _post(url: str, payload: dict, headers: dict, timeout: float):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", ACCEPT)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _message(status: int, headers: dict, raw: bytes, mid: int | None, step: str):
    """一条响应：JSON 直接解，SSE 取 id 对上的那条 data。"""
    text = raw.decode("utf-8", "replace")
    # 头名大小写不保证：uvicorn/hypercorn 发的是小写 `content-type`，`dict.get` 认
    # 大小写，取不到就会把 SSE 体当 JSON 解、报「既不是 JSON 也不是 SSE」。
    if "text/event-stream" in (_header(headers, "Content-Type") or "").lower():
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data = "".join(
                line[5:].lstrip()
                for line in block.splitlines()
                if line.startswith("data:")
            )
            if not data:
                continue
            try:
                message = json.loads(data)
            except ValueError:
                continue
            if mid is None or message.get("id") == mid:
                return message
        raise Failed(f"{step} 的 SSE 流里没有 id={mid} 的响应")
    if not text.strip():
        return None  # 通知的 202，正常
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Failed(
            f"{step} 返回 HTTP {status}，既不是 JSON 也不是 SSE：{text[:300]}"
        ) from exc


def _result(status: int, headers: dict, raw: bytes, mid: int, step: str) -> dict:
    message = _message(status, headers, raw, mid, step)
    if message is None or "result" not in message:
        detail = json.dumps(message, ensure_ascii=False) if message else "（空）"
        raise Failed(f"{step} 没有结果（HTTP {status}）：{detail[:300]}")
    return message["result"]


def run(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    call: str | None = None,
    args: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    sent = dict(headers or {})
    status, response_headers, raw = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "cheese-smoke", "version": "1"},
            },
        },
        sent,
        timeout,
    )
    result = _result(status, response_headers, raw, 1, "initialize")
    # 有状态 server 在 initialize 的响应头里给会话号，之后的请求都得带上。
    session = _header(response_headers, "Mcp-Session-Id")
    if session:
        sent["Mcp-Session-Id"] = session

    _post(
        url,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        sent,
        timeout,
    )
    tools = (
        _result(
            *_post(
                url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sent, timeout
            ),
            2,
            "tools/list",
        ).get("tools")
        or []
    )

    outcome = {
        "server": result.get("serverInfo") or {},
        "protocol": result.get("protocolVersion"),
        "session": session,
        "tools": tools,
        "call": None,
    }
    if call:
        outcome["call"] = _result(
            *_post(
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": call, "arguments": args or {}},
                },
                sent,
                timeout,
            ),
            3,
            "tools/call",
        )
    return outcome


def _print(outcome: dict) -> None:
    server = outcome["server"]
    headline = f"server: {server.get('name', '（没报名）')} {server.get('version', '')}"
    print(headline.rstrip())
    print(f"protocol: {outcome['protocol']}")
    print(f"session: {outcome['session'] or '（无状态）'}")
    tools = outcome["tools"]
    print(f"工具 {len(tools)} 个：")
    width = max((len(t["name"]) for t in tools), default=0)
    for tool in tools:
        first = (tool.get("description") or "").splitlines()[:1]
        print(f"  - {tool['name']:<{width}}  {first[0] if first else ''}")
    if outcome["call"] is not None:
        print("call:")
        print(json.dumps(outcome["call"], ensure_ascii=False, indent=2))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="smoke.py", description="给一台 MCP server 做冒烟"
    )
    parser.add_argument(
        "url", help="Streamable HTTP 端点，如 https://mcp.example.com/mcp"
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="名: 值",
        help="额外请求头，可重复（如鉴权头）",
    )
    parser.add_argument(
        "--call", default=None, metavar="工具名", help="真调一次这个工具"
    )
    parser.add_argument(
        "--args", default="{}", metavar="JSON", help="--call 的入参，缺省 {}"
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="每一步的超时秒数")
    ns = parser.parse_args(argv)

    headers: dict[str, str] = {}
    for raw in ns.header:
        name, sep, value = raw.partition(":")
        if not sep:
            print(
                f"[smoke] --header 要写成 '名: 值'，收到的是 {raw!r}", file=sys.stderr
            )
            return 2
        headers[name.strip()] = value.strip()
    try:
        args = json.loads(ns.args)
    except ValueError as exc:
        print(f"[smoke] --args 不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    try:
        _print(
            run(ns.url, headers=headers, call=ns.call, args=args, timeout=ns.timeout)
        )
    except (Failed, urllib.error.URLError, TimeoutError) as exc:
        print(f"[smoke] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
