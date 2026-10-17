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
script. `【递卡】` and `递卡` are the same tag, so a copied label is not a second label.

Whatever the caller passes, the line goes out with one prefix, never two: a body that
carries the whole line — `我是芝士。<@座位> 【递卡】…`, the shape a line is copied in —
has that prefix stripped before the wake composes its own, however many layers of it
were pasted. A `我是X。` that came in with the body stands in for `--as` when none was
given, so a pasted line still says who is speaking. Composing never doubles the prefix
and is idempotent for a body that did not itself open with one: feeding the composed
message back in reproduces it, which is what `--self-test` asserts. The body keeps
everything past the prefix — the mention a line opens its body with, the bracketed word
that is not one of the labels, a `我是…` sentence — since a mention is taken as prefix
only when a label follows it, and a label is the only thing that says where a pasted
line's prefix ended.

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
import os
import re
import sys
import urllib.error
import uuid

import platform_api
from platform_api import USER, call, login, unwrap

#: #1343, the dispatcher's own task. Overridable to rehearse against a scratch topic.
TARGET = os.environ.get("OKCHEESE_DISPATCHER", "998a4d26-a7d7-4313-b3f5-b22304182d5d")
#: 《总调度工作流 v2》§8: the four labels a line points at the dispatcher with.
LABELS = ("递卡", "交付", "待决", "队列")
#: Printed wherever the command line is wrong. One example, in the 递卡 format, because
#: the tag alone does not show what a body looks like.
USAGE = ("""usage: wake_orchestrator.py <标签> <正文> [--as <名字>] [--request-id <id>]"""
         """  (or --seat / --self-test)
  e.g. wake_orchestrator.py 递卡 "#1512 PR #3675 head 672653c5：CI 绿｜review 672653c5"""
         """ 结论 无 blocker/major｜档位 B｜碰了：无"
  the body may carry the whole line you copied — `我是芝士。<@座位> 【递卡】…` — and that
  prefix is stripped, so what goes out has one layer of it, not two.""")

#: The three pieces the wake puts in front of a body, in the shapes a pasted line has
#: them. `我是X。` is the self-identification `--as` writes, and the name is capped and
#: barred from punctuation so that a body opening with a sentence like
#: `我是说，这样不行` is not mistaken for one. The sentence has to end: with its full
#: stop, or with what only a line carries next (`<@…>`/`@词…`/`【…】`) — a body that
#: *is* such a sentence and stops there (`我是来确认的`) is body, not a name.
SELF_ID = re.compile(
    r"^我是([^\s。，,、！!？?：:；;【】<>@]{1,20})(?:。|(?=\s*(?:<@|@\w|【)))")
#: A mention, platform form first: `<@handle>` is what the compose writes and what the
#: platform renders, and the bare `@handle` is the same thing typed by hand.
MENTION = re.compile(r"^<@([^>\s]{1,60})>|^@([^\s，,。、！!？?：:；;【】<>@]{1,40})")
#: A bracketed tag word, in the shape the compose writes it.
TAG_WORD = re.compile(r"^【([^】\n]{1,24})】")
#: How many of the topic's newest messages to read back. The dispatcher's topic is busy
#: enough that another line's message can land between the post and the read; 20 leaves
#: room for that without paging.
READ_BACK = 20


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


def strip_prefix(text, tags, take_name=True):
    """(name, tag, rest): the wake's own prefix taken off the head of `text`.

    The pieces are read left to right, and each one is taken only as part of a line: a
    mention counts when a `【…】` the caller could mean comes right after it, a
    `【…】` counts when its word is one of `tags` — the four labels, plus whatever the
    caller wrote in the tag slot — and a `我是X` counts when one of those two comes next.
    So a body that opens by mentioning a person (`@李老师 【重要】…`) keeps its mention,
    and a body that opens right after the prefix's own mention (`…【递卡】@张三 请看`)
    keeps the person it names instead of losing them to the prefix. A line pasted with
    two layers of prefix comes out with none.

    What comes back is the prefix only when a tag was found in the run: the tag is what
    says where a pasted line's prefix ends, so with no tag a `我是X。` is taken on its
    own only when the body still follows it — a body that is nothing but that sentence
    (`我是来确认的`) keeps it. A tag also ends the prefix: `我是X。` past one is another
    layer only where a whole line follows it (`我是…。<@b> 【交付】…`), the seat it was
    pasted with, never the head of a body. The run goes on past either reading, so a
    body that opens with a line of its own still has that line taken off. With
    `take_name` false — the caller named the speaker with `--as` — that last reading is
    off too, since a body is then never the one who is speaking.

    Nothing else is taken. A mention is only ever taken together with the label after
    it, so a head that has one and no label (`--as 乙 递卡 "我是甲。<@a> 正文"`) keeps
    both rather than going out as a lone dangling mention."""
    def opens_a_line(s):
        """Whether `s` opens the next piece of a wake line: a label this caller could
        mean, or a mention a label follows. A mention alone is not one — a sentence may
        name someone (`我是说 @张三 请看`), and taking it for a line would eat the head
        of a body and read the speaker's name out of its first words."""
        word = TAG_WORD.match(s)
        if word and word.group(1) in tags:
            return True
        m = MENTION.match(s)
        if m:
            word = TAG_WORD.match(s[m.end():].lstrip())
            return bool(word and word.group(1) in tags)
        return False

    pieces, rest, off = [], text, 0
    while True:
        m = SELF_ID.match(rest)
        if m:
            nxt = rest[m.end():].lstrip()
            if opens_a_line(nxt) or (take_name and not pieces
                                     and rest[m.end() - 1] == "。" and nxt):
                # Either the head of a line, or `我是X。` at the very top with a body
                # after it and no line around it — the speaker's name. Both go on with
                # the run, so a body that opens with a line of its own still has that
                # line taken off (`我是甲。我是甲。【递卡】正文` leaves `正文`).
                kind, value = "self", m.group(1)
            else:
                break
        else:
            m = MENTION.match(rest)
            if m:
                word = TAG_WORD.match(rest[m.end():].lstrip())
                if not (word and word.group(1) in tags):
                    break
                kind, value = "mention", ""
            else:
                m = TAG_WORD.match(rest)
                if not (m and m.group(1) in tags):
                    break
                kind, value = "tag", m.group(1)
        raw = rest[m.end():]
        rest = raw.lstrip()
        off += m.end() + (len(raw) - len(rest))
        pieces.append((kind, value, off))
    tags_seen = [p for p in pieces if p[0] == "tag"]
    if tags_seen:
        name = next((v for k, v, _o in pieces if k == "self"), "")
        return name, tags_seen[0][1], text[pieces[-1][2]:]
    if pieces and pieces[0][0] == "self":
        return pieces[0][1], "", text[pieces[0][2]:]
    return "", "", text


def normalize(tag, body, as_name):
    """(tag, body, as_name) with any wake prefix the caller pasted on the input taken off.

    Idempotent for a body that did not itself open with a prefix: the message composed
    from this, fed back in, composes to itself, because the prefix the compose adds is
    exactly what `strip_prefix` removes. A `我是X。` pasted in stands in for `--as` when
    none was given, so a line copied whole still names whoever wrote it."""
    tag = tag.strip().strip("【】").strip()
    name, body_tag, rest = strip_prefix(body, set(LABELS) | {tag}, take_name=not as_name)
    if body_tag and body_tag != tag:
        print(f"note: the body says 【{body_tag}】 and the tag argument says {tag}; "
              f"sending 【{tag}】", file=sys.stderr)
    if tag not in LABELS:
        # The tag slot got the line instead of the line's first word: take the label
        # out of it and keep what followed as the head of the body.
        t_name, t_tag, t_rest = strip_prefix(tag, set(LABELS))
        if t_tag:
            name, tag = name or t_name, t_tag
            rest = f"{t_rest}\n{rest}" if t_rest and rest else (t_rest or rest)
    return tag, rest, as_name or name


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
        # Which argument is missing, not just that the line is wrong: a pasted line has
        # the tag inside it, so "one argument" is the common shape of this mistake.
        if len(rest) < 2:
            print(f"a wake needs <标签> and <正文> as two arguments; got {len(rest)}",
                  file=sys.stderr)
        else:
            print(f"a wake takes <标签> and <正文> and nothing else; extra: "
                  f"{' '.join(repr(a) for a in rest[2:])}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return None, None, as_name, request_id, flags
    tag, body, as_name = normalize(rest[0], rest[1], as_name)
    if not tag or any(c.isspace() for c in tag):
        print(f"标签 has to be one word: {rest[0]!r} leaves {tag!r}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return None, None, as_name, request_id, flags
    return tag, body, as_name, request_id, flags


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

        # `login` resolves `call` in `platform_api`'s globals, so the module's own
        # binding alone would leave the login call on the network.
        real_call, real_platform_call = call, platform_api.call
        real_password = os.environ.get("OKCHEESE_PASS")
        globals()["call"] = fake_call
        platform_api.call = fake_call
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
            platform_api.call = real_platform_call
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

    def missing_body():
        """One positional argument is a pasted line missing its 正文: name the argument
        that is missing, not just that the line is wrong."""
        rc, err, seen = drive(["递卡"])
        return rc, "got 1" in err, "usage:" in err, seen["calls"] == []

    def too_many_args():
        rc, err, _ = drive(["递卡", "正文", "又一段"])
        return rc, "nothing else" in err, "usage:" in err

    def tag_not_a_word():
        """A tag with a blank in it is neither a label nor a fifth label — it would
        break the one-line prefix — so it is refused while parsing."""
        rc, err, seen = drive(["递 卡", "正文"])
        return rc, "one word" in err, seen["calls"] == []

    def pasted_line_once():
        """The reported shape: the caller pastes the whole line into the body. What is
        posted carries the prefix once, and the pasted 我是芝士。 is who spoke."""
        rc, err, seen = drive(["递卡", "我是芝士。<@cheese-abc> 【递卡】#1512 PR #3675"])
        return rc, seen.get("body", {}).get("content", "")

    def tag_mismatch():
        """A pasted body whose 【标签】 disagrees with the argument: the argument wins,
        the body loses its bracketed word, and the disagreement is said out loud."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = parse_args(["递卡", "【交付】结论"])[:2]
        return got, "交付" in err.getvalue()

    def roundtrip(tag, body, as_name=""):
        """(the composed message, the message composed from its own output).

        These are the same string unless the prefix the compose adds is left on the
        body instead of stripped — the duplicate this script removes — so this is the
        case that has to go red if the stripping stops working."""
        first = wake_line("cheese-seat", tag, body, as_name=as_name)
        return first, wake_line("cheese-seat", *parse_args([tag, first])[:3])

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
        ("退出码：只给一个位置参数 → 2，说清缺的是正文并给出用法",
         missing_body(), (2, True, True, True)),
        ("退出码：多给位置参数 → 2，说清多的那一段是什么",
         too_many_args(), (2, True, True)),
        ("退出码：标签里带空格 → 2，不该拿它去拼前缀",
         tag_not_a_word(), (2, True, True)),
        ("行程：正文粘着整句前缀 → 发出去的只有一层，名字取自粘进来的那句",
         pasted_line_once(), (0, "我是芝士。<@cheese-seat> 【递卡】#1512 PR #3675")),
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
        ("前缀：纯正文原样，一点都不去动",
         strip_prefix("正文", LABELS), ("", "", "正文")),
        ("前缀：粘进来的整句剥成（名字、标签、正文）",
         strip_prefix("我是芝士。<@cheese-abc> 【递卡】正文", LABELS),
         ("芝士", "递卡", "正文")),
        ("前缀：两层前缀一次剥干净（点名和标签各重复一次也一样）",
         strip_prefix("我是芝士。<@a> 【递卡】我是芝士。<@b> 【递卡】正文", LABELS),
         ("芝士", "递卡", "正文")),
        ("前缀：没带 我是X。、点名后面跟标签的也剥",
         strip_prefix("@cheese-abc 【递卡】正文", LABELS), ("", "递卡", "正文")),
        ("前缀：单带一个【标签】的也剥（标签写成 递卡 或【递卡】都是它）",
         [strip_prefix("【交付】结论", LABELS),
          parse_args(["【递卡】", "正文"])[:2]],
         [("", "交付", "结论"), ("递卡", "正文")]),
        ("前缀：正文开头 @ 一个人时不当点名吃掉（后面不是标签）",
         strip_prefix("@李老师 【重要】这份方案请过一眼", LABELS),
         ("", "", "@李老师 【重要】这份方案请过一眼")),
        ("前缀：正文开头自带的【…】不是四个标签之一就留着",
         strip_prefix("【重要】结论", LABELS), ("", "", "【重要】结论")),
        ("前缀：前缀之后正文自己 @ 的人留着，连后面的逗号一起",
         [strip_prefix("我是芝士。<@a> 【递卡】@张三 请看这条", LABELS)[2],
          strip_prefix("我是芝士。<@a> 【递卡】@张三，请看一下", LABELS)[2]],
         ["@张三 请看这条", "@张三，请看一下"]),
        ("前缀：前缀之后平台写法 <@某人> 也留着（那是给人点名的，不是前缀）",
         strip_prefix("我是芝士。<@a> 【递卡】<@张三> 请看这条", LABELS)[2],
         "<@张三> 请看这条"),
        ("前缀：正文里后面还跟点名时只取最前面那一个，中间的不动",
         strip_prefix("我是芝士。<@a> 【递卡】@张三 <@李四> 请看", LABELS)[2],
         "@张三 <@李四> 请看"),
        ("前缀：没有标签时，光一个 我是X。 不当名字吃（那条话就是正文）",
         [strip_prefix("我是来确认的", LABELS), strip_prefix("我是来确认的。", LABELS),
          strip_prefix("我是说，这样不行", LABELS)],
         [("", "", "我是来确认的"), ("", "", "我是来确认的。"), ("", "", "我是说，这样不行")]),
        ("前缀：没有标签、但 我是X。 后面还有正文的，名字照取",
         [strip_prefix("我是芝士。正文", LABELS), strip_prefix("我是芝士。@张三 请看", LABELS)],
         [("芝士", "", "正文"), ("芝士", "", "@张三 请看")]),
        ("前缀：正文自己是一句 我是… 的话时，它后面跟的点名不算前缀（不伪造说话人）",
         [strip_prefix("我是说 @张三 请看", LABELS),
          strip_prefix("我是说 <@张三> 请看", LABELS),
          strip_prefix("我是甲@张三 请看", LABELS),
          strip_prefix("我是甲 @张三。看这条", LABELS),
          strip_prefix("我是甲 @张三 【重要】x", LABELS)],
         [("", "", "我是说 @张三 请看"), ("", "", "我是说 <@张三> 请看"),
          ("", "", "我是甲@张三 请看"), ("", "", "我是甲 @张三。看这条"),
          ("", "", "我是甲 @张三 【重要】x")]),
        ("前缀：同上，一路走到参数里也不吃掉开头（给了 --as 更不该认名字）",
         [parse_args(["递卡", "我是说 @张三 请看"])[:3],
          parse_args(["--as", "甲", "递卡", "我是甲 <@a> 请处理"])[:3],
          parse_args(["--as", "甲", "递卡", "我是甲 @张三 请看"])[:3]],
         [("递卡", "我是说 @张三 请看", ""), ("递卡", "我是甲 <@a> 请处理", "甲"),
          ("递卡", "我是甲 @张三 请看", "甲")]),
        ("前缀：前缀之后正文自己是一句 我是…。 时整句留着（不当前缀的第二层）",
         [strip_prefix("我是甲。<@a> 【递卡】我是来确认的。", LABELS),
          strip_prefix("我是甲。<@a> 【递卡】我是来确认的", LABELS),
          strip_prefix("我是芝士。<@a> 【递卡】我是来确认的。请看一下这条", LABELS)],
         [("甲", "递卡", "我是来确认的。"), ("甲", "递卡", "我是来确认的"),
          ("芝士", "递卡", "我是来确认的。请看一下这条")]),
        ("前缀：同一句后面跟的吃点名不算第二层，整句留着（连点名一起）",
         [strip_prefix("我是甲。<@a> 【递卡】我是来确认的。@张三 请看", LABELS),
          strip_prefix("我是甲。<@a> 【递卡】我是来确认的。@张三，请看", LABELS)],
         [("甲", "递卡", "我是来确认的。@张三 请看"),
          ("甲", "递卡", "我是来确认的。@张三，请看")]),
        ("前缀：两层粘贴时第二层开头的 我是X。 照剥（后面接着点名和标签）",
         strip_prefix("我是甲。<@a> 【递卡】我是乙。<@b> 【交付】结论", LABELS),
         ("甲", "递卡", "结论")),
        ("前缀：第二层没有点名的两层名字也剥净（旧版会把第二层当正文留下）",
         [strip_prefix("我是甲。我是乙。<@a> 【递卡】正文", LABELS),
          strip_prefix("我是甲。我是甲。【递卡】正文", LABELS),
          strip_prefix("我是甲。我是甲。【递卡】", LABELS)],
         [("甲", "递卡", "正文"), ("甲", "递卡", "正文"), ("甲", "递卡", "")]),
        ("前缀：两层名字后面只是正文时，只取第一层当名字，第二层留正文里",
         strip_prefix("我是甲。我是甲。正文", LABELS),
         ("甲", "", "我是甲。正文")),
        ("前缀：剥过一遍的东西再剥一遍不变（幂等）",
         [strip_prefix(strip_prefix(t, LABELS)[2], LABELS)[2] for t in
          ["我是芝士。<@a> 【递卡】正文", "@cheese-abc 【递卡】正文", "正文",
           "@李老师 【重要】这份方案请过一眼", "我是芝士。<@a> 【递卡】@张三 请看这条"]],
         ["正文", "正文", "正文", "@李老师 【重要】这份方案请过一眼", "@张三 请看这条"]),
        ("前缀：正文粘着整句 → 标签用参数里的，正文只剩正文，名字顶上 --as",
         parse_args(["递卡", "我是芝士。<@cheese-abc> 【递卡】#1512 PR #3675"])[:3],
         ("递卡", "#1512 PR #3675", "芝士")),
        ("前缀：给了 --as 就不再用粘进来的名字（名字不同才看得出谁赢）",
         parse_args(["--as", "甲", "交付", "我是乙。<@a> 【交付】结论"])[:3],
         ("交付", "结论", "甲")),
        ("前缀：给了 --as 时正文只当正文，一句 我是… 的话不会被吃掉",
         [parse_args(["--as", "甲", "递卡", "我是来确认的"])[:3],
          parse_args(["--as", "甲", "递卡", "我是来确认的。请看一下这条"])[:3],
          parse_args(["--as", "甲", "交付", "我是乙。结论"])[:3],
          parse_args(["--as", "甲", "递卡", "我是甲。<@a> 正文"])[:3],
          parse_args(["--as", "甲", "递卡", "我是甲。【重要】结论"])[:3],
          parse_args(["--as", "芝士", "递卡", "我是甲。<@a> 【递卡】正文"])[:3]],
         [("递卡", "我是来确认的", "甲"), ("递卡", "我是来确认的。请看一下这条", "甲"),
          ("交付", "我是乙。结论", "甲"), ("递卡", "我是甲。<@a> 正文", "甲"),
          ("递卡", "我是甲。【重要】结论", "甲"),
          ("递卡", "正文", "芝士")]),
        ("前缀：标签位置收到整句时，标签从里面取，剩下的是正文",
         parse_args(["我是芝士。<@a> 【递卡】#1512 PR #3675", ""])[:3],
         ("递卡", "#1512 PR #3675", "芝士")),
        ("前缀：标签位置收到整句、正文另有一段时两段都留下（换行接上）",
         parse_args(["我是芝士。<@a> 【递卡】#1512 PR #3675", "还有一句"])[:3],
         ("递卡", "#1512 PR #3675\n还有一句", "芝士")),
        ("前缀：粘进来的【标签】和参数不一致时按参数发，并在 stderr 说一句",
         tag_mismatch(), (("递卡", "结论"), True)),
        ("前缀：发出去的消息喂回来再组一次是同一个（去重坏掉时这条会红）",
         [a == b for a, b in [roundtrip("递卡", "#1512 PR #3675 head 672653c5"),
                              roundtrip("交付", "结论", as_name="芝士"),
                              roundtrip("待决", "要定什么｜我倾向 A\n第二行", as_name="芝士"),
                              roundtrip("递卡", "@张三 请看这条 head 672653c5"),
                              roundtrip("递卡", "我是来确认的"),
                              roundtrip("递卡", "我是来确认的。"),
                              roundtrip("递卡", "我是来确认的。请看一下这条"),
                              roundtrip("递卡", "我是来确认的。请看一下这条", as_name="甲"),
                              roundtrip("递卡", "我是甲。", as_name="芝士")]],
         [True, True, True, True, True, True, True, True, True]),
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
