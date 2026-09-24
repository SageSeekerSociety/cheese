"""cheese CLI raw-api escape hatch (fusion-design §5.2): arg parsing + wiring.

The `cheese` script has no .py extension (it's mounted into the sandbox as a
bare executable), so it's loaded by path.
"""

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    # The `cheese` script has no .py extension, so an explicit source loader is
    # needed (spec_from_file_location can't infer one from the suffix).
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_members_reads_the_current_topic_roster(monkeypatch, capsys):
    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "members"])
    calls = []

    def request(method, path):
        calls.append((method, path))
        return {
            "data": {
                "data": [{"member_handle": "alice", "name": "Alice", "role": "owner"}]
            }
        }

    monkeypatch.setattr(cli, "_call", request)
    cli.main()
    assert calls == [("GET", "/topics/room/members")]
    assert "Alice（owner）→ 在消息里写 <@alice>" in capsys.readouterr().out


def test_direct_mcp_request_plans_cover_only_http_operations():
    cli = _load()
    env = {
        "CHEESE_TOPIC": "room",
        "CHEESE_PROJECT": "project",
        "CHEESE_MEMORY_SCOPE": "project",
    }
    arguments = {
        "cheese_ask": {"question": "Pick", "option": ["a", "b"]},
        "cheese_deliver_at": {"at": "2026-09-21T14:00:00+00:00", "content": "look"},
        "cheese_machine": {"profile": "cloud", "device_id": None},
        "cheese_note": {"thread": "other-thread", "content": "note"},
        "cheese_close_task": {"task_id": "task", "conclusion": "done"},
        "cheese_decision": {"text": "chosen"},
        "cheese_fetch": {"url": "https://example.test", "prompt": None},
        "cheese_feedback_propose": {
            "title": "listing came back short",
            "kind": "bug",
            "visibility": "team",
            "user_said": "用户没有就这个问题说过话",
        },
        "cheese_members": {},
        "cheese_milestone": {"title": "ship", "due": "2026-09-14"},
        "cheese_notify": {"title": "notice"},
        "cheese_recall": {"query": "term"},
        "cheese_remember": {"fact": "fact", "core": False},
        "cheese_status": {},
        "cheese_tell": {"target": "task", "message": "update"},
        "cheese_title": {"text": "title", "task": None},
    }
    # 平台 MCP 上那六样里的每一个 `cheese_*` 都要有计划：没有计划的那一个，
    # 会话侧打不出去，而它恰恰是机器离线时唯一还能用的那一批（结论 21）。
    platform = {t for t in cli.PLATFORM_TOOLS.names() if t.startswith("cheese_")}
    assert platform <= set(arguments), platform - set(arguments)
    plans = {
        tool: cli.request_plan(tool, values, env) for tool, values in arguments.items()
    }
    assert all(plan["method"] in {"GET", "POST", "PUT"} for plan in plans.values())
    assert plans["cheese_decision"] == {
        "method": "POST",
        "path": "/topics/room/decision",
        "body": {"decision": "chosen"},
    }
    assert plans["cheese_milestone"]["body"]["due_date"] == ("2026-09-14T00:00:00Z")


def test_everyone_outranks_the_private_chats_personal_memory():
    """私聊里的 `remember --everyone` 写的是文档，不是对这一位的个人记忆。

    说了「所有人」，就不是只记给眼前这一位看的。两路的差别在发出去的那一刻就定
    了：写错的那一条落进只有这条会话读得到的池子，没人会发现它本该在总览文档里。
    """
    cli = _load()
    env = {
        "CHEESE_TOPIC": "room",
        "CHEESE_PROJECT": "project",
        "CHEESE_MEMORY_SCOPE": "personal",
        "CHEESE_OWNER": "alice",
    }

    everyone = cli.request_plan(
        "cheese_remember", {"fact": "x", "core": False, "everyone": True}, env
    )
    assert everyone["body"] == {"content": "x", "topic": "room", "scope": "everyone"}

    # 对照：同一个私聊里不说「所有人」的那一条，照旧是对 alice 的个人记忆。
    personal = cli.request_plan(
        "cheese_remember", {"fact": "x", "core": False, "everyone": False}, env
    )
    assert personal["body"] == {
        "content": "x",
        "topic": "room",
        "scope": "user",
        "owner": "alice",
    }


def test_artifact_publishes_the_local_file_from_a_subdirectory(monkeypatch, tmp_path):
    cli = _load()
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "report.html").write_text("<h1>Published result</h1>")
    monkeypatch.chdir(folder)
    monkeypatch.setenv("CHEESE_WORKTREE_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "show", "report.html"])
    calls = []
    monkeypatch.setattr(cli, "_call", lambda *args: calls.append(args))

    cli.main()

    assert calls == [
        (
            "POST",
            "/topics/room/shown",
            {
                "path": "site/report.html",
                "as": "html",
                "content": "<h1>Published result</h1>",
            },
        )
    ]


def test_serve_declares_only_the_port_and_registers_the_app(monkeypatch):
    cli = _load()
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "serve", "5173", "Vue dev server"])
    calls = []
    api_calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(cli, "_call", lambda *args: api_calls.append(args) or {})
    cli.main()
    assert calls == [["sh", "/preview-up", "5173"]]
    assert api_calls == [
        ("POST", "/topics/room/shown", {"path": "Vue dev server", "as": "app"})
    ]


@pytest.mark.parametrize(
    "base",
    ("http://host.docker.internal:8099", "https://cheese.example/api"),
)
def test_api_root_preserves_the_selected_transport_surface(monkeypatch, base):
    cli = _load()
    monkeypatch.setattr(cli, "API", base)
    assert cli._api_root() == base


def test_api_subcommand_parses_method_and_path(monkeypatch):
    cli = _load()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_raw_request",
        lambda m, p, d, o=None: captured.update(method=m, path=p, data=d, out=o),
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "api", "GET", "/topics/x/blocks"])
    cli.main()
    assert captured == {
        "method": "GET",
        "path": "/topics/x/blocks",
        "data": None,
        "out": None,
    }


def test_api_subcommand_passes_data(monkeypatch):
    cli = _load()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_raw_request",
        lambda m, p, d, o=None: captured.update(method=m, path=p, data=d, out=o),
    )
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["cheese", "api", "POST", "/topics/x/title", "--data", '{"title":"hi"}'],
    )
    cli.main()
    assert captured["method"] == "POST"
    assert captured["data"] == '{"title":"hi"}'


def test_api_malformed_data_exits(monkeypatch):
    cli = _load()
    monkeypatch.setattr(cli, "_raw_request", lambda *a: None)
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "api", "POST", "/x", "--data", "not-json"]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_api_no_args_prints_index(monkeypatch):
    cli = _load()
    called: dict[str, bool] = {}
    monkeypatch.setattr(cli, "_print_api_index", lambda: called.update(hit=True))
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "api"])
    cli.main()
    assert called.get("hit") is True


def test_raw_request_sends_cheese_token(monkeypatch):
    """The escape hatch carries the container's auth — NOT a no-auth backdoor."""
    cli = _load()
    monkeypatch.setattr(cli, "API", "http://backend/api")
    monkeypatch.setattr(cli, "TOKEN", "tok-123")
    monkeypatch.setattr(cli, "TURN", "")
    seen: dict[str, object] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["token"] = req.get_header("X-cheese-token")
        return _Resp()

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    cli._raw_request("get", "topics/x/blocks", None)  # leading slash added
    assert seen["url"] == "http://backend/api/topics/x/blocks"
    assert seen["method"] == "GET"
    assert seen["token"] == "tok-123"


def test_status_subcommand_calls_status_endpoint(monkeypatch, capsys):
    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    seen: dict[str, object] = {}

    def fake_call(method: str, path: str, body: dict | None = None) -> dict:
        seen.update(method=method, path=path, body=body)
        return {"data": {"topic": {"title": "修东西", "status": "active"}}}

    monkeypatch.setattr(cli, "_call", fake_call)
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "status"])
    cli.main()
    assert seen["method"] == "GET"
    assert seen["path"] == "/topics/t-1/status"
    assert "修东西" in capsys.readouterr().out


def _status_payload(turn: dict) -> dict:
    return {
        "topic": {"title": "T", "status": "active", "branch": "topic/x"},
        "turn": turn,
        "cards": [
            {
                "status": "gate_failed",
                "reviewer": "alice",
                "gate_output_tail": "FAIL: ruff\nResult: 0/3 passed",
            }
        ],
        "platform": {
            "active_turns": 1,
            "queued_turns": 0,
            "disk": {"free_gb": 10.0, "total_gb": 100.0, "used_pct": 90},
            "credits": {"unlimited": True},
        },
    }


def test_format_status_renders_cards_and_waterlines():
    cli = _load()
    out = cli._format_status(
        _status_payload({"status": "running", "near_ceiling": False, "activity": None})
    )
    assert "正常运行中" in out
    assert "闸门输出（尾部）" in out
    assert "Result: 0/3 passed" in out
    assert "已用 90%" in out
    assert "额度: 不限" in out


def test_format_status_never_renders_a_live_countdown():
    """A literal "还剩 Ns" figure was observed making the agent rush against what
    is only meant to be a wedged-turn safety net. Neither running state may
    reintroduce it."""
    cli = _load()
    for turn in (
        {"status": "running", "near_ceiling": False},
        {"status": "running", "near_ceiling": True},
    ):
        out = cli._format_status(_status_payload(turn))
        assert "还剩" not in out
        assert "budget" not in out


def test_format_status_renders_near_ceiling_state():
    cli = _load()
    out = cli._format_status(
        _status_payload({"status": "running", "near_ceiling": True})
    )
    assert "接近硬顶" in out


# --- the self-describing layer -------------------------------------------
#
# CLAUDE.md's rule is "prose documents only what --help cannot tell you". That
# only holds if --help actually tells you something: the layer that can never go
# stale (it is generated from this code) used to be empty at the subcommand
# level, so every semantic lived in prose that drifted. These two tests keep it
# from emptying out again.


def test_accept_request_without_a_subject_never_reaches_the_backend(
    monkeypatch, capsys
):
    """The subject is required, and the CLI must refuse BEFORE the POST — a card
    that is already filed cannot be un-filed, so a warning printed afterwards
    (which is what this used to do) taught nobody anything."""
    cli = _load()
    calls: list[tuple] = []
    monkeypatch.setattr(cli, "_call", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "accept-request", "alice", "最懂"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code != 0
    assert calls == []
    assert "--subject" in capsys.readouterr().err


def test_accept_request_sends_the_subject_it_was_given(monkeypatch):
    cli = _load()
    monkeypatch.setattr(cli, "_task_id", lambda _: "task-1")
    monkeypatch.setattr(cli, "_sync_task", lambda _: None)
    sent: list[dict] = []

    def _call(m, p, d=None):
        sent.append({"p": p, "d": d})
        return {"data": {"reviewer_handle": "alice"}}

    monkeypatch.setattr(cli, "_call", _call)
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "cheese",
            "accept-request",
            "alice",
            "最懂",
            "--subject",
            "fix(accept): require a commit subject",
            "--artifact",
            "结题报告",
        ],
    )

    cli.main()

    [call] = sent
    assert call["p"] == "/topics/t-1/tasks/task-1/accept-card"
    assert call["d"]["change_subject"] == "fix(accept): require a commit subject"
    assert call["d"]["reviewer_handle"] == "alice"
    # 交付说明本次更新的是哪一项产物 (#1085 结论三)。
    assert call["d"]["artifact"] == "结题报告"


def test_ready_never_syncs_creates_a_card_or_merges(monkeypatch):
    cli = _load()
    calls = []
    monkeypatch.setattr(cli, "TOPIC", "room-1")
    monkeypatch.setattr(cli, "_task_id", lambda _: "task-1")
    monkeypatch.setattr(cli, "_sync_task", lambda _: pytest.fail("ready must not sync"))
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *args: calls.append(args) or {"data": {"ready": True, "pr_number": 1}},
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "ready"])
    cli.main()
    assert calls == [("POST", "/topics/room-1/tasks/task-1/ready")]


def test_accept_request_without_a_reviewer_lets_the_backend_pick_the_default(
    monkeypatch,
):
    """未指定验收人 = 用项目默认验收人 (#718 设置表).

    The CLI must not invent a value for the field — not the empty string
    either. "Nobody was named" and "somebody typed an empty name" have to stay
    distinguishable at the backend, because only one of them may fall through
    to the project default.
    """
    cli = _load()
    monkeypatch.setattr(cli, "_task_id", lambda _: "task-1")
    monkeypatch.setattr(cli, "_sync_task", lambda _: None)
    sent: list[dict] = []

    def _call(m, p, d=None):
        sent.append({"p": p, "d": d})
        return {"data": {"reviewer_handle": "bob"}}

    monkeypatch.setattr(cli, "_call", _call)
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["cheese", "accept-request", "--subject", "fix(x): y", "--artifact", "报告"],
    )

    cli.main()

    [call] = sent
    assert "reviewer_handle" not in call["d"]


def _subparsers(parser):
    """(name, parser) for every subcommand, minus the internal `__`-prefixed ones."""
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return [
                (name, sub)
                for name, sub in action.choices.items()
                if not name.startswith("__")
            ]
    raise AssertionError("no subparsers on this parser")


def test_every_subcommand_says_what_it_is_for():
    """`cheese <cmd> --help` must be worth reading on its own.

    `help=` only shows up in the parent's listing; `description=` is what a
    reader of `cheese <cmd> --help` actually gets. A subcommand with neither is
    a bare list of argument names.
    """
    cli = _load()
    bare = [
        name
        for name, sub in _subparsers(cli.build_parser())
        if not (sub.description or "").strip()
    ]
    assert not bare, f"subcommands with no --help description: {bare}"


def test_every_argument_says_what_it_takes():
    """Positionals are the easiest thing to leave unexplained — `title`, `fact`,
    `reviewer` name a slot without saying what belongs in it."""
    cli = _load()
    parser = cli.build_parser()
    undocumented = []
    for name, sub in _subparsers(parser):
        targets = [(name, sub)]
        # `doc` nests another level (doc set / doc get).
        targets += [(f"{name} {n}", s) for n, s in _subparsers_or_empty(sub)]
        for label, p in targets:
            for action in p._actions:
                if action.dest in ("help", "version") or action.dest == "==SUPPRESS==":
                    continue
                if _is_subparsers(action):
                    continue
                if not (action.help or "").strip():
                    undocumented.append(f"{label}:{action.dest}")
    assert not undocumented, f"arguments with no help=: {undocumented}"


class _FakeHTTPResponse:
    """Just enough of an http response for `json.load(r)` inside a `with`."""

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def read(self, *_args) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def test_token_export_command_is_not_exposed():
    with pytest.raises(SystemExit) as error:
        _load().build_parser().parse_args(["gh-token"])
    assert error.value.code == 2


def _is_subparsers(action):
    import argparse

    return isinstance(action, argparse._SubParsersAction)


def _subparsers_or_empty(parser):
    try:
        return _subparsers(parser)
    except AssertionError:
        return []


# --- 实况文档的写入版本 ---------------------------------------------------
#
# The living doc is replaced whole, so a `doc set` based on a version somebody
# has already moved past destroys their edit outright. The CLI's job is to make
# the version a fact about what the agent READ, never something it can state.


def _doc_cli(monkeypatch, tmp_path, calls):
    cli = _load()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(cli, "AUTHOR", "cheese")

    def fake_call(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if method == "GET":
            return {"data": {"content": "# 现在的文档", "doc_version": 7}}
        return {"data": {"doc_version": 8}}

    monkeypatch.setattr(cli, "_call", fake_call)
    return cli


def test_a_set_without_a_read_claims_no_version(monkeypatch, tmp_path, capsys):
    """Never having read the doc is version 0 — which the platform accepts only
    when there is no doc yet. Writing over a document you have not read is the
    whole failure, so the CLI must not invent a number that lets it through."""
    calls: list[tuple] = []
    cli = _doc_cli(monkeypatch, tmp_path, calls)
    doc = tmp_path / "d.md"
    doc.write_text("# 我写的", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "doc", "set", str(doc)])

    cli.main()

    assert calls[-1][2]["expected_version"] == 0


def test_a_set_writes_against_the_version_get_showed(monkeypatch, tmp_path, capsys):
    """`doc get` is what earns the write: the version it printed is the one the
    following `doc set` is based on."""
    calls: list[tuple] = []
    cli = _doc_cli(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "doc", "get"])
    cli.main()
    assert "# 现在的文档" in capsys.readouterr().out

    doc = tmp_path / "d.md"
    doc.write_text("# 现在的文档\n\n加一段", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "doc", "set", str(doc)])
    cli.main()

    assert calls[-1][2]["expected_version"] == 7


def test_a_refused_set_says_how_to_recover(monkeypatch, tmp_path, capsys):
    """A rejection has to leave the agent knowing what to do next. Retrying the
    same file is refused identically, forever — the way out is re-reading."""
    cli = _load()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *a, **k: {
            "ok": False,
            "status": 409,
            "error": {"data": {"doc_version": 9}},
        },
    )
    doc = tmp_path / "d.md"
    doc.write_text("# 旧的", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "doc", "set", str(doc)])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "第 9 版" in err
    assert "cheese doc get" in err


def test_a_won_set_remembers_the_version_it_produced(monkeypatch, tmp_path, capsys):
    """Two `doc set` calls in one turn is normal. The second is based on what
    the first produced — asking the agent to re-read its own write would be
    ceremony, and forgetting would reject it."""
    calls: list[tuple] = []
    cli = _doc_cli(monkeypatch, tmp_path, calls)
    doc = tmp_path / "d.md"
    doc.write_text("# 一稿", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "doc", "get"])
    cli.main()
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "doc", "set", str(doc)])
    cli.main()
    cli.main()

    assert [c[2]["expected_version"] for c in calls if c[0] == "PUT"] == [7, 8]


def test_feedback_propose_refuses_locally_when_there_is_no_topic():
    """`cheese feedback propose` posts to `/topics/{topic}/feedback-proposals`.

    With nothing in `CHEESE_TOPIC` that path is `/topics//feedback-proposals`,
    which the server answers 404 — and the CLI then reports *the command* as
    having failed, exit code 1, with the server's 「话题不存在」 as the reason
    (`_call` exits on any non-2xx). The refusal belongs where the missing thing
    is known: here, before the request, naming the variable that is empty.

    Both halves asserted: the refusal, and the path it guards. A guard that also
    broke the working case would otherwise read as a passing test.
    """
    cli = _load()
    args = {
        "title": "沙箱里 make 装不上依赖",
        "kind": "bug",
        "visibility": "public",
        "user_said": "用户没有就这个问题说过话",
    }

    with pytest.raises(ValueError, match="Missing CHEESE_TOPIC"):
        cli.request_plan("cheese_feedback_propose", args, {"CHEESE_PROJECT": "p"})

    plan = cli.request_plan(
        "cheese_feedback_propose",
        args,
        {"CHEESE_TOPIC": "room", "CHEESE_PROJECT": "p"},
    )
    assert plan["method"] == "POST"
    assert plan["path"] == "/topics/room/feedback-proposals"
    assert plan["body"]["title"] == "沙箱里 make 装不上依赖"


@pytest.mark.parametrize(
    ("delivered", "expected"),
    [
        (True, "便条已递给那条线程。"),
        (False, "那条线程这会儿没有在跑的轮次,便条没人接住。"),
    ],
)
def test_note_says_whether_anyone_caught_it(monkeypatch, capsys, delivered, expected):
    """`delivered` 是这条工具的答案本身，不是一个可以丢掉的状态码。

    便条直接进那条线程正在跑的那一轮，那边这一刻没在跑就没人接住。一律打「已递」
    的话，用 Bash 调这条命令的那条线程会当作对面已经知道了往下走 —— 而那句话其实
    掉在地上了，两边都不会有人再提起它。
    """
    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "note", "other-thread", "看一眼 CI"]
    )
    monkeypatch.setattr(
        cli, "_call", lambda *args, **kwargs: {"data": {"delivered": delivered}}
    )

    cli.main()

    assert capsys.readouterr().out.strip() == expected


def test_the_feedback_tool_says_when_to_use_it():
    """这个工具**唯一的说明就是那段文字**，所以触发时机必须出现在模型读到的工具说明里。

    这里钉的是一次真事故：四条触发时机原来写在 `feedback` 那个**父** parser 的
    `description` 上，而翻 argparse 树的那条路（`cli_worker._tools()`：
    `leaf.description or leaf.format_usage()`）只产出**叶子** —— 于是那段字一个字都
    没到过模型。工具建好了、流程接好了、限流也在，而模型从来不知道什么时候该用它。

    两条发现路径各读一处，所以要两边都断言，断言的是**模型实际读到的那一份**：

    * **会话侧那张常量表**（`PLATFORM_TOOLS`）—— claude_code 读的是它（结论 21：表在
      会话层，机器离线时它也在）。
    * **`_tools()` 从 argparse 树翻出来的工具说明** —— pi 在机器上生成 catalog、codex
      从执行器的 `native` 服务器发现，两条都读这里。注意不是裸的 `leaf.description`：
      `_tools()` 会把祖先 parser 的 description 拼上去，那才是工具 schema 里的字面。

    只改一处的话，模型看到的是两种说法里的随机一种，而且**是哪个取决于它跑在哪个
    harness 上** —— 那种 bug 只有对着某一个 harness 复现得出来。
    """
    from app.domain.agent import cli_worker

    cli = _load()

    from_table = next(
        tool
        for tool in cli.PLATFORM_TOOLS.schemas()
        if tool["name"] == "cheese_feedback_propose"
    )["description"]

    from_tree = next(
        tool["description"]
        for tool in cli_worker._tools(cli.build_parser())
        if tool["name"] == "cheese_feedback_propose"
    )

    assert from_table, "会话侧那张表里没有这一条"
    assert from_tree, "argparse 树翻出来的工具说明里没有这一条"

    for description in (from_table, from_tree):
        # 什么时候该提（四条触发时机里至少要能读出这些）。
        assert "反复失败" in description
        # 什么时候不该提 —— 「平台坏了」和「用户不会用」之间那句话。
        assert "用户的使用方式" in description
        # 不要打断：这条卡是提案，不是发布。
        assert "中途" in description


def test_machine_reports_its_session_choice(monkeypatch, capsys):
    cli = _load()
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "machine", "device", "--device-id", "workstation"]
    )
    monkeypatch.setattr(
        cli,
        "_planned_call",
        lambda *_: {
            "data": {
                "session": {
                    "choice": {
                        "name": "Workstation",
                        "profile": "device",
                        "device_id": "workstation",
                    }
                }
            }
        },
    )
    cli.main()
    output = capsys.readouterr().out
    assert "Workstation" in output
    assert "文件不会自动迁移" in output
    assert "None" not in output
    assert "点头" not in output


def test_sync_agents_writes_and_prunes_model_definitions(monkeypatch, tmp_path):
    """项目模型目录（本池）每项各得一份 model-<name>.md（名字、一句话描述、
    model=目录 id）；不再可指定的（带记号的）被清掉；上一版按队友写的
    mate-*.md 无条件清掉；手写的同名前缀文件和其他人的 agent 文件不动。

    响应形状照 `ok(state)` 的真实信封：data 就是 state 本身（不是分页的
    data.data）——形状搞错的代价是目录恒空、一份文件不写还全清。"""
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", "proj")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "sync-agents"])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def request(method, path, body=None, **kwargs):
        assert (method, path) == ("GET", "/projects/proj/default-model")
        return {
            "data": {
                "model": "deepseek-flash",
                "subagent_model": None,
                "pool": "gateway",
                "choices": [
                    {
                        "id": "deepseek-flash",
                        "label": "DeepSeek Flash",
                        "supply": "gateway",
                        "default": True,
                    },
                    {
                        "id": "kimi-k3",
                        "label": "Kimi K3",
                        "supply": "gateway",
                        "default": False,
                    },
                    {
                        "id": "MiMo/V2.6 Pro",
                        "label": "MiMo",
                        "supply": "gateway",
                        "default": False,
                    },
                    {
                        "id": "sonnet",
                        "label": "Sonnet",
                        "supply": "subscription",
                        "default": False,
                    },
                ],
            }
        }

    monkeypatch.setattr(cli, "_call", request)
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "mate-cheese.md").write_text("上一版按队友写的")
    (agents / "model-old.md").write_text(
        "由 cheese sync-agents 按项目模型目录生成，别手改。\n旧内容"
    )
    (agents / "model-manual.md").write_text("手写的，别碰")
    (agents / "keep.md").write_text("someone else's file")
    cli.main()
    kimi = (agents / "model-kimi-k3.md").read_text()
    assert "name: kimi-k3" in kimi
    assert "model: kimi-k3" in kimi
    assert "平台模型目录 · Kimi K3（kimi-k3）" in kimi
    assert (agents / "model-deepseek-flash.md").exists()
    # CC 分身名比模型 id 严格：名字是清洗过的,真 id 在 frontmatter 的 model 里。
    mimo = (agents / "model-mimo-v2.6-pro.md").read_text()
    assert "name: mimo-v2.6-pro" in mimo
    assert "model: MiMo/V2.6 Pro" in mimo
    # 池外的不写：订阅短名对这个 gateway 项目不可指定。
    assert not (agents / "model-sonnet.md").exists()
    assert not (agents / "mate-cheese.md").exists()
    # 带记号的旧文件清掉；手写的同名前缀文件不动。
    assert not (agents / "model-old.md").exists()
    assert (agents / "model-manual.md").read_text() == "手写的，别碰"
    assert (agents / "keep.md").read_text() == "someone else's file"


def test_sync_agents_falls_back_to_default_choice_for_pool(monkeypatch, tmp_path):
    """旧后端的响应里没有 pool 字段：退回「默认那一项的供给」；一个默认项
    都没有时宁可不列也不按错池列。"""
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", "proj")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "sync-agents"])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def request(method, path, body=None, **kwargs):
        return {
            "data": {
                "choices": [
                    {
                        "id": "sonnet",
                        "label": "Sonnet",
                        "supply": "subscription",
                        "default": True,
                    },
                    {
                        "id": "kimi-k3",
                        "label": "Kimi",
                        "supply": "gateway",
                        "default": False,
                    },
                ]
            }
        }

    monkeypatch.setattr(cli, "_call", request)
    cli.main()
    agents = tmp_path / "agents"
    assert (agents / "model-sonnet.md").exists()
    assert not (agents / "model-kimi-k3.md").exists()


def test_sync_agents_never_breaks_the_session(monkeypatch, tmp_path, capsys):
    """它只是发现层（闸在准入）：后端够不着时打一行警告，恒退出 0。"""
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", "proj")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "sync-agents"])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def boom(method, path, body=None, **kwargs):
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr(cli, "_call", boom)
    cli.main()
    assert "sync-agents 跳过" in capsys.readouterr().err
