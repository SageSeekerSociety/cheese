"""The `cheese` CLI on the machine: what it still does, and what it no longer
offers.

The script has no .py extension (it is installed as a bare executable), so it
is loaded by path.
"""

import importlib.util
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


#: Pure platform actions are session-side tools now (结论 63), and the CLI copy
#: of a tool the table already had is gone. None of these may come back as a
#: subcommand: a second way to do the same thing is the one that dies with the
#: machine.
_NOT_ON_THE_MACHINE = (
    "chat",
    "doc",
    "task",
    "close-task",
    "accept-request",
    "describe",
    "ready",
    "tell",
    "milestone",
    "title",
    "decision",
    "remember",
    "recall",
    "notify",
    "fetch",
    "lock",
    "unlock",
    "members",
    "status",
    "ask",
    "feedback",
    "machine",
    "note",
    "deliver-at",
    "api",
    "split",
)


@pytest.mark.parametrize("command", _NOT_ON_THE_MACHINE)
def test_a_platform_action_is_not_a_subcommand(command):
    with pytest.raises(SystemExit) as error:
        _load().build_parser().parse_args([command])
    assert error.value.code == 2


def test_the_library_on_the_machine_only_fetches():
    with pytest.raises(SystemExit) as error:
        _load().build_parser().parse_args(["library", "ls"])
    assert error.value.code == 2


def test_worktree_prepares_the_task_directory(monkeypatch, tmp_path, capsys):
    """开活是会话侧的 `cheese_task`；目录在机器上，由这一条准备。"""
    cli = _load()
    prepared = []

    def worktree(task_id):
        prepared.append(task_id)
        return tmp_path / task_id

    monkeypatch.setattr(cli, "_task_worktree", worktree)
    task = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "worktree", task])
    cli.main()
    assert prepared == [task]
    assert capsys.readouterr().out.strip() == str(tmp_path / task)


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
