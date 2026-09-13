#!/usr/bin/env python3
"""Forward an executor CLI invocation without loading the command implementation."""

import array
import json
import os
import socket
import sys
import urllib.error
import urllib.request
import uuid


def _chat_send_argv(argv):
    if len(argv) < 3 or argv[:2] != ["chat", "send"]:
        return None
    if any(token in {";", "&&", "||", "|", ">", ">>", "<", "<<"} for token in argv):
        return None
    if any(any(char in token for char in ";&|<>`$\n") for token in argv):
        return None
    if "--help" in argv or "--file" in argv:
        return None
    reply_to = None
    request_id = None
    content = []
    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "--reply-to" and index + 1 < len(argv):
            reply_to = argv[index + 1]
            index += 2
        elif token == "--request-id" and index + 1 < len(argv):
            request_id = argv[index + 1]
            index += 2
        elif token.startswith("-"):
            return None
        else:
            content.append(token)
            index += 1
    if len(content) != 1 or not content[0].strip():
        return None
    return content[0], reply_to, request_id


def _publish_chat_locally(parsed):
    if parsed is None:
        return None
    content, reply_to, request_id = parsed
    api = os.environ.get("CHEESE_API", "").rstrip("/")
    token = os.environ.get("CHEESE_TOKEN", "")
    topic = os.environ.get("CHEESE_TOPIC", "")
    if not api or not token or not topic:
        return None
    request_id = request_id or str(uuid.uuid4())
    try:
        uuid.UUID(request_id)
    except ValueError:
        print("[cheese] --request-id must be a UUID", file=sys.stderr)
        return 2
    payload = {"content": content, "request_id": request_id}
    if reply_to:
        payload["reply_to"] = reply_to
    request = urllib.request.Request(
        f"{api}/topics/{topic}/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Cheese-Token": token},
        method="POST",
    )
    if os.environ.get("CHEESE_TURN"):
        request.add_header("X-Cheese-Turn", os.environ["CHEESE_TURN"])
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        print(f"[cheese] chat send failed: {error}", file=sys.stderr)
        return 1
    print(f"[cheese] request_id={request_id}", file=sys.stderr)
    print(json.dumps(result.get("data", result), ensure_ascii=False))
    return 0


def main():
    try:
        parsed = _chat_send_argv(sys.argv[1:])
    except (ValueError, IndexError):
        parsed = None
    status = _publish_chat_locally(parsed)
    if status is not None:
        raise SystemExit(status)
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(os.environ["CHEESE_CLI_SOCKET"])
        connection.sendmsg(
            [b"\0"],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [0, 1, 2]))],
        )
        connection.sendall(
            json.dumps(
                {
                    "argv": sys.argv[1:],
                    "cwd": os.getcwd(),
                    "env": dict(os.environ),
                    "stdio": [
                        {"encoding": stream.encoding, "errors": stream.errors}
                        for stream in (sys.stdin, sys.stdout, sys.stderr)
                    ],
                }
            ).encode()
            + b"\n"
        )
        with connection.makefile("rb") as stream:
            line = stream.readline()
        if not line:
            raise SystemExit(
                "[cheese] 常驻进程已断开；请先查询原请求结果，再决定是否重试。"
            )
        raise SystemExit(json.loads(line)["status"])


if __name__ == "__main__":
    main()
