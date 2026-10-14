#!/usr/bin/env python3
"""Wake the dispatcher: the four steps a line used to hand-roll, in one command.

  OKCHEESE_USER=wangchangxin OKCHEESE_PASS=<password> \
      python3 .claude/scripts/wake_orchestrator.py <标签> <正文> [--as <名字>]

  OKCHEESE_PASS=... python3 .claude/scripts/wake_orchestrator.py --seat
  OKCHEESE_PASS=... python3 .claude/scripts/wake_orchestrator.py --dry-run <标签> <正文>
  python3 .claude/scripts/wake_orchestrator.py --self-test      (offline: no token, no network)

It logs in as the dev account, reads the dispatcher task's current seat, posts
`<@seat> 【tag】body` there with a request_id, and reads the message back. The exit
status says which step failed, so a caller can branch on it:

  0 posted and read back · 2 the command line or the environment is wrong ·
  3 the platform refused a call · 4 posted but the read-back did not show it

The tag is one of the four labels 《总调度工作流 v2》§8 gives a line — 递卡, 交付, 待决,
队列 — and the body follows that section's format for it, e.g. 递卡's
`#任务 PR #号 head <8位>：CI 绿｜review <8位> 结论 …｜档位 A–G｜碰了：…`. A tag outside
the four is passed through with a warning rather than refused: the labels are the
workflow's convention, and a line that needs a fifth one should not be blocked by this
script.

A run that failed after posting (exit 4) can be retried with `--request-id <id>`: the
platform returns the message it already stored for that id instead of a second one, so
the retry confirms the same message rather than sending its twin.

Credentials come from the environment, never from argv (ps shows it) and never from a
file: OKCHEESE_PASS is required, OKCHEESE_USER defaults to the dev account's name. That
account displays as 王长鑫, so `--as 芝士` puts which teammate is speaking in front of
the mention; the 递卡 format's #任务 does the same job for that label.
"""
import contextlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
USER = os.environ.get("OKCHEESE_USER", "wangchangxin")
#: #1343, the dispatcher's own task. Overridable to rehearse against a scratch topic.
TARGET = os.environ.get("OKCHEESE_DISPATCHER", "998a4d26-a7d7-4313-b3f5-b22304182d5d")
#: 《总调度工作流 v2》§8: the four labels a line points at the dispatcher with.
LABELS = ("递卡", "交付", "待决", "队列")
#: How many of the topic's newest messages to read back. The dispatcher's topic is busy
#: enough that another line's message can land between the post and the read; 20 leaves
#: room for that without paging.
READ_BACK = 20


def call(method, path, token=None, body=None, timeout=30):
    """One platform call. Raises OSError/HTTPError; returns the parsed JSON body."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def unwrap(x):
    """The payload of a platform response: `{"data": …}`, however many times nested."""
    while isinstance(x, dict) and "data" in x:
        x = x["data"]
    return x


def login(user, password):
    return unwrap(call("POST", "/users/auth/login",
                       body={"username": user, "password": password}))["accessToken"]


def seat_of(topic, token):
    """The handle of the seat the task is on right now, or None when it has none."""
    return unwrap(call("GET", f"/topics/{topic}/task", token)).get("agent_handle")


def wake_line(seat, tag, body, as_name=""):
    """The message to post: the mention the platform needs, the workflow's own word for
    why the line is speaking, and the body as the line wrote it."""
    head = f"我是{as_name}。" if as_name else ""
    return f"{head}<@{seat}> 【{tag}】{body}"


def confirmed(blocks, message_id):
    """Whether what we posted is among the blocks the platform just returned."""
    return any((b or {}).get("id") == message_id for b in blocks or [])


def parse_args(argv):
    """(tag, body, as_name, request_id, flags). tag/body are None when the line is wrong."""
    flags, rest, as_name, request_id, i = set(), [], "", "", 0
    valued = {"--as": "as", "--request-id": "req"}
    while i < len(argv):
        a = argv[i]
        if a in valued and i + 1 < len(argv):
            if valued[a] == "as":
                as_name = argv[i + 1]
            else:
                request_id = argv[i + 1]
            i += 2
        elif a.startswith("--"):
            flags.add(a)
            i += 1
        else:
            rest.append(a)
            i += 1
    if flags - {"--self-test", "--seat", "--dry-run"}:
        print(f"unknown flag(s): {' '.join(sorted(flags))}", file=sys.stderr)
        return None, None, as_name, request_id, flags
    if flags & {"--self-test", "--seat"}:
        return "", "", as_name, request_id, flags
    if len(rest) != 2:
        print("usage: wake_orchestrator.py <标签> <正文> [--as <名字>] [--request-id <id>]"
              "  (or --seat / --self-test)", file=sys.stderr)
        return None, None, as_name, request_id, flags
    return rest[0], rest[1], as_name, request_id, flags


def self_test():
    """(name, got, want) for the parts that need no platform. The cases that exercise a
    wrong command line print to stderr by design; that noise is swallowed here so the
    run still reads as one line of output."""
    with contextlib.redirect_stderr(io.StringIO()):
        cases = [
        ("消息：点名当班座位，标签在方括号里，正文原样",
         wake_line("cheese-abc", "递卡", "#1512 PR #3675 head 672653c5：CI 绿"),
         "<@cheese-abc> 【递卡】#1512 PR #3675 head 672653c5：CI 绿"),
        ("消息：给了名字就把名字放在最前面（开发号显示的是王长鑫）",
         wake_line("cheese-abc", "交付", "结论", as_name="芝士"),
         "我是芝士。<@cheese-abc> 【交付】结论"),
        ("消息：正文里的方括号和换行原样带出去",
         wake_line("s", "待决", "要定什么｜我倾向 A\n第二行"),
         "<@s> 【待决】要定什么｜我倾向 A\n第二行"),
        ("回读：刚发的 id 出现在返回的块里才算数",
         [confirmed([{"id": "a"}, {"id": "b"}], "b"), confirmed([{"id": "a"}], "b")],
         [True, False]),
        ("回读：空列表和空块都不算数",
         [confirmed([], "b"), confirmed([None], "b")], [False, False]),
        ("信封：套了几层 data 都取到里面那层",
         [unwrap({"data": {"data": {"id": "x"}}}), unwrap({"id": "x"})],
         [{"id": "x"}, {"id": "x"}]),
        ("命令行：标签和正文是两个位置参数",
         parse_args(["递卡", "#1512 PR #3675"])[:2], ("递卡", "#1512 PR #3675")),
        ("命令行：只给一个位置参数不认",
         parse_args(["递卡"])[:2], (None, None)),
        ("命令行：--as 吃掉下一个词，不算位置参数",
         parse_args(["--as", "芝士", "递卡", "正文"])[:3], ("递卡", "正文", "芝士")),
        ("命令行：--request-id 原样收下（重试时用同一个 id 不会发第二条）",
         parse_args(["--request-id", "abc123", "递卡", "正文"])[3], "abc123"),
        ("命令行：不认识的开关直接说",
         parse_args(["--wat", "递卡", "正文"])[:2], (None, None)),
        ("命令行：--seat 不用位置参数",
         parse_args(["--seat"])[4], {"--seat"}),
    ]
    bad = 0
    for name, got, want in cases:
        if got != want:
            bad += 1
            print(f"FAIL {name}\n  想要 {want!r}\n  读成 {got!r}")
    if bad:
        print(f"self-test: {bad} of {len(cases)} failed")
        return 1
    print(f"self-test: ok ({len(cases)} cases, offline)")
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    tag, body, as_name, request_id, flags = parse_args(argv)
    if tag is None:
        return 2
    password = os.environ.get("OKCHEESE_PASS", "")
    if not password:
        print("OKCHEESE_PASS is not set; the password never goes in argv or a file",
              file=sys.stderr)
        return 2
    if tag and tag not in LABELS:
        print(f"warning: {tag} is not one of the four labels {LABELS}", file=sys.stderr)
    try:
        token = login(USER, password)
        seat = seat_of(TARGET, token)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        print(f"the platform refused a call: {e!r}", file=sys.stderr)
        return 3
    if not seat:
        print(f"no seat on {TARGET}; nothing to wake", file=sys.stderr)
        return 3
    if "--seat" in flags:
        print(seat)
        return 0
    content = wake_line(seat, tag, body, as_name)
    if "--dry-run" in flags:
        print(f"would send to {TARGET}: {content}")
        return 0
    try:
        sent = unwrap(call("POST", f"/topics/{TARGET}/messages", token,
                           {"content": content, "request_id": request_id or uuid.uuid4().hex}))
        back = unwrap(call("GET", f"/topics/{TARGET}/blocks?kind=message&limit={READ_BACK}",
                           token))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        print(f"the platform refused a call: {e!r}", file=sys.stderr)
        return 3
    if not confirmed(back if isinstance(back, list) else [], sent.get("id")):
        print(f"posted {sent.get('id')} but the read-back did not show it", file=sys.stderr)
        return 4
    print(f"woke {seat} on {TARGET}: {sent.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
