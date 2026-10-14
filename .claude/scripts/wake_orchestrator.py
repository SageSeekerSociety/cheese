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
  3 the platform refused a call · 4 posted but the read-back did not show it ·
  5 the dispatcher's topic has no seat. Exit 3 on the posting call does not prove the
  message is absent: an answer lost after the platform stored it looks the same.

The tag is one of the four labels 《总调度工作流 v2》§8 gives a line — 递卡, 交付, 待决,
队列 — and the body follows that section's format for it, e.g. 递卡's
`#任务 PR #号 head <8位>：CI 绿｜review <8位> 结论 …｜档位 A–G｜碰了：…`. A tag outside
the four is passed through with a warning rather than refused: the labels are the
workflow's convention, and a line that needs a fifth one should not be blocked by this
script.

A run that failed after posting is retried with `--request-id <uuid>`, the same id the
first run used: the platform returns the message it already stored for that id instead of
a second one, so the retry confirms the same message rather than sending its twin. Two
prints mean "a message may be on the platform now" — exit 4, and exit 3 on the posting
call itself, whose answer may have been lost after the platform stored the message — and
both say which id to reuse, so nothing has to be re-derived. The id has to be a UUID: the
endpoint types the field that way, and it is written in the canonical form here, so the
retry matches whatever form the first run was given.

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
    """Whether what we posted is among the blocks the platform just returned.

    A reply with no id of its own proves nothing, and `None == None` would read a block
    that also carries no id as that message — so an id-less reply is never a match."""
    if message_id is None:
        return False
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
    if "--self-test" in flags:
        return "", "", as_name, request_id, flags
    if "--seat" in flags:
        # Asked with a tag and a body, `--seat` would print the seat and exit 0 without
        # posting: a line's wake gone, reported as a success. Say so instead.
        if rest:
            print("--seat takes no tag or body; drop it to send a wake", file=sys.stderr)
            return None, None, as_name, request_id, flags
        return "", "", as_name, request_id, flags
    if request_id:
        try:
            # Canonical form on purpose: pydantic's UUID field refuses strings that
            # `uuid.UUID` accepts (`{hex-without-dashes}`, `urn:uuid:hex-without-dashes`),
            # and the platform dedupes on this string, so the posting call and any retry
            # have to agree on it.
            request_id = str(uuid.UUID(request_id))
        except ValueError:
            print(f"--request-id must be a uuid; {request_id!r} is not one", file=sys.stderr)
            return None, None, as_name, request_id, flags
    if len(rest) != 2:
        print("usage: wake_orchestrator.py <标签> <正文> [--as <名字>] [--request-id <id>]"
              "  (or --seat / --self-test)", file=sys.stderr)
        return None, None, as_name, request_id, flags
    return rest[0], rest[1], as_name, request_id, flags


def self_test():
    """(name, got, want) for everything that needs no platform. The cases that exercise a
    wrong command line print to stderr by design; that noise is swallowed here so the run
    still reads as one line of output. The exit-code cases drive `main` with `call` stubbed
    out, so the codes the docstring promises are covered without a network."""

    def drive(argv, password="pw", seat="cheese-seat", post=None, back=None):
        """(exit code, stderr, what the stub saw) for one run of `main`.

        `post`/`back`, when they are exceptions, are raised by the stub in place of that
        call's answer, so a failure can be put on either call — or on both — offline."""
        stubbed = {"calls": []}

        def fake_call(method, path, token=None, body=None, timeout=30):
            stubbed["calls"].append(f"{method} {path}")
            if path.endswith("/users/auth/login"):
                return {"data": {"accessToken": "tok"}}
            if path.endswith("/task"):
                return {"data": {"agent_handle": seat}}
            if method == "POST":
                stubbed["body"] = body
                if isinstance(post, BaseException):
                    raise post
                return {"data": {"id": "m1"}}
            if isinstance(back, BaseException):
                raise back
            return {"data": [{"id": "m1"}] if back is None else back}

        real_call, real_password = call, os.environ.get("OKCHEESE_PASS")
        globals()["call"] = fake_call
        os.environ["OKCHEESE_PASS"] = password
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = main(argv)
        finally:
            globals()["call"] = real_call
            if real_password is None:
                os.environ.pop("OKCHEESE_PASS", None)
            else:
                os.environ["OKCHEESE_PASS"] = real_password
        return rc, err.getvalue(), stubbed

    refused = urllib.error.HTTPError("stub", 503, "no", {}, None)
    rid = "3e310fb7-c3d3-4667-a4d5-552783b38975"

    def happy():
        rc, err, seen = drive(["递卡", "正文"])
        return rc, "body" in seen, err

    def post_refused():
        """The call may have landed before it failed, so the id has to come out."""
        rc, err, _ = drive(["递卡", "正文", "--request-id", rid], post=refused)
        return rc, rid in err

    def read_back_failed():
        rc, err, _ = drive(["递卡", "正文", "--request-id", rid], back=refused)
        return rc, rid in err

    def read_back_did_not_show_it():
        rc, err, _ = drive(["递卡", "正文", "--request-id", rid], back=[])
        return rc, "did not show it" in err

    def no_seat():
        rc, err, _ = drive(["递卡", "正文"], seat=None)
        return rc, "no seat" in err

    def no_password():
        rc, err, _ = drive(["递卡", "正文"], password="")
        return rc, "OKCHEESE_PASS" in err

    def seat_only():
        rc, err, seen = drive(["--seat"])
        return rc, err, "body" in seen

    def dry_run():
        rc, err, seen = drive(["--dry-run", "递卡", "正文"])
        return rc, "body" in seen

    def unknown_tag():
        rc, err, seen = drive(["甩锅", "正文"])
        return rc, "warning" in err, "body" in seen

    runtime_cases = [
        ("退出码：发出去并回读到这条 → 0，没有别的报错", happy(), (0, True, "")),
        ("退出码：平台拒绝 POST → 3，并带上 request_id（它可能已经存下了）",
         post_refused(), (3, True)),
        ("退出码：POST 之后回读失败 → 4，并带上 request_id 供重试",
         read_back_failed(), (4, True)),
        ("退出码：回读里没有这条 → 4",
         read_back_did_not_show_it(), (4, True)),
        ("退出码：话题没有座位 → 5", no_seat(), (5, True)),
        ("退出码：环境里没有密码 → 2", no_password(), (2, True)),
        ("行程：--seat 只打座位，不发消息", seat_only(), (0, "", False)),
        ("行程：--dry-run 只组消息，不发消息", dry_run(), (0, False)),
        ("行程：标签不在四个里也照发，只在 stderr 说一句", unknown_tag(), (0, True, True)),
    ]

    with contextlib.redirect_stderr(io.StringIO()):
        cases = runtime_cases + [
        ("消息：点名当班座位，标签在方括号里，正文原样",
         wake_line("cheese-abc", "递卡", "#1512 PR #3675 head 672653c5：CI 绿"),
         "<@cheese-abc> 【递卡】#1512 PR #3675 head 672653c5：CI 绿"),
        ("消息：给了名字就把名字放在最前面（开发号显示的是王长鑫）",
         wake_line("cheese-abc", "交付", "结论", as_name="芝士"),
         "我是芝士。<@cheese-abc> 【交付】结论"),
        ("消息：正文里的方括号和换行原样带出去",
         wake_line("s", "待决", "要定什么｜我倾向 A\n第二行"),
         "<@s> 【待决】要定什么｜我倾向 A\n第二行"),
        ("回读：刚发的 id 出现在返回的块里才算数（落在第一个也算）",
         [confirmed([{"id": "b"}, {"id": "a"}], "b"), confirmed([{"id": "a"}, {"id": "b"}], "b"),
          confirmed([{"id": "a"}], "b")],
         [True, True, False]),
        ("回读：空列表和空块都不算数",
         [confirmed([], "b"), confirmed([None], "b")], [False, False]),
        ("回读：回复里没有 id 时不许当成功（None 不等于 None）",
         [confirmed([{}], None), confirmed([{"id": None}], None)], [False, False]),
        ("信封：套了几层 data 都取到里面那层",
         [unwrap({"data": {"data": {"id": "x"}}}), unwrap({"id": "x"})],
         [{"id": "x"}, {"id": "x"}]),
        ("命令行：标签和正文是两个位置参数",
         parse_args(["递卡", "#1512 PR #3675"])[:2], ("递卡", "#1512 PR #3675")),
        ("命令行：只给一个位置参数不认",
         parse_args(["递卡"])[:2], (None, None)),
        ("命令行：--as 吃掉下一个词，不算位置参数",
         parse_args(["--as", "芝士", "递卡", "正文"])[:3], ("递卡", "正文", "芝士")),
        ("命令行：--request-id 收下 uuid（重试时用同一个 id 不会发第二条）",
         parse_args(["--request-id", "3e310fb7-c3d3-4667-a4d5-552783b38975", "递卡", "正文"])[3],
         "3e310fb7-c3d3-4667-a4d5-552783b38975"),
        ("命令行：--request-id 不是 uuid 就当场拦下（平台收不了它）",
         parse_args(["--request-id", "abc123", "递卡", "正文"])[:2], (None, None)),
        ("命令行：--request-id 收下不带连字符的 uuid，也归成规范写法（重试才对得上）",
         parse_args(["--request-id", "{3e310fb7c3d34667a4d5552783b38975}", "递卡", "正文"])[3],
         "3e310fb7-c3d3-4667-a4d5-552783b38975"),
        ("命令行：不认识的开关直接说",
         parse_args(["--wat", "递卡", "正文"])[:2], (None, None)),
        ("命令行：尾上没有值的开关不认（--as / --request-id 都要吃一个词）",
         [parse_args(["递卡", "--as"])[:2], parse_args(["递卡", "正文", "--request-id"])[:2]],
         [(None, None), (None, None)]),
        ("命令行：--seat 单独用才取座位，带着位置参数就是错的",
         [parse_args(["--seat"])[:2], parse_args(["递卡", "正文", "--seat"])[:2]],
         [("", ""), (None, None)]),
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
        return 5
    if "--seat" in flags:
        print(seat)
        return 0
    content = wake_line(seat, tag, body, as_name)
    if "--dry-run" in flags:
        print(f"would send to {TARGET}: {content}")
        return 0
    request_id = request_id or uuid.uuid4().hex
    try:
        sent = unwrap(call("POST", f"/topics/{TARGET}/messages", token,
                           {"content": content, "request_id": request_id}))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        # The call may have landed before it failed: a timeout, or a 5xx raised after the
        # platform stored the message. So the id goes out with it — a retry that resends
        # it cannot post a twin, and one that mints a fresh id can.
        print(f"the platform refused a call: {e!r} (request_id {request_id})", file=sys.stderr)
        return 3
    message_id = sent.get("id") if isinstance(sent, dict) else None
    # From here on the message is on the platform, so a failure is exit 4, not 3: a
    # caller that retries on 3 would post its twin.
    try:
        back = unwrap(call("GET", f"/topics/{TARGET}/blocks?kind=message&limit={READ_BACK}",
                           token))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        print(f"posted {message_id} (request_id {request_id}) but the read-back failed: {e!r}",
              file=sys.stderr)
        return 4
    if not confirmed(back if isinstance(back, list) else [], message_id):
        print(f"posted {message_id} (request_id {request_id}) but the read-back did not show it",
              file=sys.stderr)
        return 4
    print(f"woke {seat} on {TARGET}: {message_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
