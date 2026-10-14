#!/usr/bin/env python3
"""Wake the dispatcher: the four steps a line used to hand-roll, in one command.

  OKCHEESE_USER=wangchangxin OKCHEESE_PASS=<password> \
      python3 .claude/scripts/wake_orchestrator.py <标签> <正文> [--as <名字>]

  OKCHEESE_PASS=... python3 .claude/scripts/wake_orchestrator.py --seat
  OKCHEESE_PASS=... python3 .claude/scripts/wake_orchestrator.py --dry-run <标签> <正文>
  python3 .claude/scripts/wake_orchestrator.py --self-test      (offline: no token, no network)

It logs in as the dev account, reads the seat the dispatcher task is on, posts
`<@seat> 【tag】body` there with a request_id, and reads the message back. The exit
status says which step failed, so a caller can branch on it:

  0 posted and read back (also `--seat`, which prints a seat, and `--dry-run`, which
  composes the message without sending it) · 2 the command line or the environment is
  wrong · 3 the platform refused a call · 4 posted but the read-back did not show it ·
  5 no seat to name. Exit 3 on the posting call does not prove the message is absent: an
  answer lost after the platform stored it looks the same.

A task row with no seat of its own is not "nothing to wake" — the platform hands such a
task to the seat its room answers for — so the room's roster is read next, and `seat_of`
says which row it takes and when it stands down. Exit 5 means this cannot name that seat:
the platform would still work the task, under a teammate that cannot be picked out from
here.

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
endpoint types the field that way, and the forms this prints are ones a retry can feed
straight back — a `--request-id` is held as the canonical uuid, one minted here as its
dashed-free hex, and either is accepted by the parser.

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
    """The handle of the seat to mention on this line, or None when none can be named.

    A task row that names a seat is used as it stands. A row with no seat of its own
    (`agent_handle` is `None`) is not left without one either: the platform works such a
    task under the seat its room answers for (`AgentInstanceService.for_task` →
    `for_topic`), so the room's roster is read next and its single AI teammate is that
    seat. With none, or with several, which teammate the room answers for is not something
    a roster read can tell, and a wrong name wakes the wrong one — so it names no seat
    rather than guess."""
    given = unwrap(call("GET", f"/topics/{topic}/task", token))
    given = given.get("agent_handle") if isinstance(given, dict) else None
    if given:
        return given
    rows = unwrap(call("GET", f"/topics/{topic}/members", token))
    # Both shapes are guarded rather than assumed: a payload that is not what the route
    # documents would otherwise raise out of `main`, leaving a traceback where the exit
    # status is supposed to say which step failed.
    seats = [r.get("member_handle") for r in (rows if isinstance(rows, list) else [])
             if isinstance(r, dict) and r.get("agent")]
    return seats[0] if len(seats) == 1 else None


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
            # `uuid.UUID` accepts (`{hex-without-dashes}`, `urn:uuid:hex-without-dashes`).
            # The platform keys the stored message on `str()` of the field either way, so
            # the one shape is what keeps the id this run prints the string a retry hands
            # back unchanged.
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

    unset = object()

    def drive(argv, password="pw", seat="cheese-seat", roster=None, post=None, back=None,
              login=None, task=None, task_raw=unset):
        """(exit code, stderr, what the stub saw) for one run of `main`.

        `roster` is what the room's member list answers with when the task names no seat
        (`None` stands for the task row naming one, so the roster is not read at all), and
        `task_raw` puts a payload there that is not the documented one. `login`/`task`/
        `post`/`back`, when they are exceptions, are raised by the stub in place of that
        call's answer — one per platform call, so a failure can be put on any of the four
        offline."""
        stubbed = {"calls": []}

        def fake_call(method, path, token=None, body=None, timeout=30):
            stubbed["calls"].append(f"{method} {path}")
            if path.endswith("/users/auth/login"):
                if isinstance(login, BaseException):
                    raise login
                return {"data": {"accessToken": "tok"}}
            if path.endswith("/members"):
                return {"data": {"data": roster if roster is not None else [], "total": 0}}
            if path.endswith("/task"):
                if isinstance(task, BaseException):
                    raise task
                return {"data": {"agent_handle": seat} if task_raw is unset else task_raw}
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
                try:
                    rc = main(argv)
                except Exception as e:  # a crash is a failed case, not a broken suite
                    rc = f"raised {type(e).__name__}"
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

    def roster_only():
        """No seat on the row, one teammate on the roster: that one is who to mention."""
        rc, err, seen = drive(["递卡", "正文"], seat=None,
                              roster=[{"member_handle": "cheese-abc", "agent": True}])
        return rc, "cheese-abc" in seen.get("body", {}).get("content", "")

    def roster_several():
        """Two teammates and no seat on the row: the platform's own choice is not
        something this can tell, so it stands down instead of guessing a name."""
        rc, err, seen = drive(["递卡", "正文"], seat=None, roster=[
            {"member_handle": "cheese-abc", "agent": True},
            {"member_handle": "cheese-def", "agent": True},
        ])
        return rc, "body" in seen, "cannot name a seat" in err

    def no_seat_anywhere():
        rc, err, _ = drive(["递卡", "正文"], seat=None,
                           roster=[{"member_handle": "someone", "agent": False}])
        return rc, "cannot name a seat" in err

    def task_payload_not_a_row():
        """A `/task` payload that is not the documented row is no seat, not a traceback."""
        rc, err, _ = drive(["递卡", "正文"], task_raw=None)
        return rc, "cannot name a seat" in err

    def roster_rows_of_junk():
        """Rows that are not objects are skipped; the one real teammate is still found."""
        rc, err, seen = drive(["递卡", "正文"], seat=None,
                              roster=[None, "oops", {"member_handle": "cheese-abc",
                                                     "agent": True}])
        return rc, "cheese-abc" in seen.get("body", {}).get("content", "")

    def no_password():
        rc, err, _ = drive(["递卡", "正文"], password="")
        return rc, "OKCHEESE_PASS" in err

    def bad_command_line():
        """A wrong command line is exit 2 too, and no call is made to find that out."""
        rc, err, seen = drive(["递卡"])
        return rc, seen["calls"] == []

    def login_refused():
        rc, err, seen = drive(["递卡", "正文"], login=refused)
        return rc, "refused" in err, "body" in seen

    def seat_read_refused():
        rc, err, _ = drive(["递卡", "正文"], task=refused)
        return rc, "refused" in err

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
        ("座位：任务行没写座位、名册上只有一个队友 → 点名它，照发",
         roster_only(), (0, True)),
        ("座位：名册上有好几个队友、任务行也没写 → 不猜，说没有可叫的",
         roster_several(), (5, False, True)),
        ("退出码：哪一处都点不出一个队友 → 5",
         no_seat_anywhere(), (5, True)),
        ("座位：任务行回的不是一个对象 → 当作没写座位，不抛异常",
         task_payload_not_a_row(), (5, True)),
        ("座位：名册里混着不是对象的行 → 跳过它们，仍点出那个队友",
         roster_rows_of_junk(), (0, True)),
        ("退出码：环境里没有密码 → 2", no_password(), (2, True)),
        ("退出码：命令行不对 → 2，且一个调用都不发", bad_command_line(), (2, True)),
        ("退出码：登录被拒 → 3，什么都没发出去",
         login_refused(), (3, True, False)),
        ("退出码：读座位被拒 → 3", seat_read_refused(), (3, True)),
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
        print(f"cannot name a seat to wake on {TARGET}: the task names none and the "
              f"room's roster does not name exactly one teammate — the task is still "
              f"worked, so wake its teammate by hand", file=sys.stderr)
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
