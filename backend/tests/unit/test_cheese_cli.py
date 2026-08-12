"""cheese CLI raw-api escape hatch (fusion-design §5.2): arg parsing + wiring.

The `cheese` script has no .py extension (it's mounted into the sandbox as a
bare executable), so it's loaded by path.
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


def test_api_root_strips_api_prefix(monkeypatch):
    cli = _load()
    monkeypatch.setattr(cli, "API", "http://host.docker.internal:8099/api")
    assert cli._api_root() == "http://host.docker.internal:8099"
    # No /api suffix → returned unchanged.
    monkeypatch.setattr(cli, "API", "http://localhost:9000")
    assert cli._api_root() == "http://localhost:9000"


def test_api_subcommand_parses_method_and_path(monkeypatch):
    cli = _load()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli, "_raw_request", lambda m, p, d: captured.update(method=m, path=p, data=d)
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "api", "GET", "/topics/x/blocks"])
    cli.main()
    assert captured == {"method": "GET", "path": "/topics/x/blocks", "data": None}


def test_api_subcommand_passes_data(monkeypatch):
    cli = _load()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli, "_raw_request", lambda m, p, d: captured.update(method=m, path=p, data=d)
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
    """turn 活跃度检测 (2026-08-09): a literal "还剩 Ns" figure was observed
    making the agent rush against what's only meant to be a wedged-turn safety
    net — the three-state rendering must never reintroduce it."""
    cli = _load()
    for turn in (
        {"status": "running", "near_ceiling": False, "activity": None},
        {"status": "running", "near_ceiling": True, "activity": None},
        {
            "status": "running",
            "near_ceiling": False,
            "activity": {"idle_for_s": 400, "suspect_since_s_ago": 120},
        },
    ):
        out = cli._format_status(_status_payload(turn))
        assert "还剩" not in out
        assert "budget" not in out


def test_format_status_renders_idle_suspect_state():
    cli = _load()
    out = cli._format_status(
        _status_payload(
            {
                "status": "running",
                "near_ceiling": False,
                "activity": {"idle_for_s": 320, "suspect_since_s_ago": 120},
            }
        )
    )
    assert "疑似卡死" in out
    assert "2 分钟" in out  # 120s → 2min, rounded


def test_format_status_renders_near_ceiling_state():
    cli = _load()
    out = cli._format_status(
        _status_payload({"status": "running", "near_ceiling": True, "activity": None})
    )
    assert "接近硬顶" in out


# --- cheese await: 后台跑长任务，跑完平台叫醒本话题 ---------------------------


def test_await_registers_then_forks_and_returns_immediately(monkeypatch, tmp_path):
    """`cheese await` must not block — that's the whole point. It registers the
    command, hands the wake token to a detached child, and returns."""
    import subprocess

    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", "topic-1")
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[tuple] = []
    monkeypatch.setattr(
        cli,
        "_call",
        lambda m, p, b=None, **kw: (
            calls.append((m, p, b)),
            {
                "data": {
                    "task_id": "task-9",
                    "wake_token": "wake-tok",
                    "label": "全量检查",
                }
            },
        )[1],
    )
    spawned: dict = {}

    def fake_popen(argv, **kw):
        spawned["argv"] = argv
        spawned["kw"] = kw
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["cheese", "await", "bash check.sh", "--label", "全量检查", "--timeout", "900"],
    )
    cli.main()

    method, path, body = calls[0]
    assert (method, path) == ("POST", "/topics/topic-1/background-task")
    assert body["command"] == "bash check.sh"
    assert body["label"] == "全量检查"
    assert body["timeout_s"] == 900

    assert spawned["argv"][2] == "__await-child"
    assert spawned["argv"][3] == "task-9"
    assert spawned["argv"][4] == "bash check.sh"
    # The wake token rides in the env, never on a world-readable argv.
    assert spawned["kw"]["env"]["CHEESE_AWAIT_TOKEN"] == "wake-tok"
    assert "wake-tok" not in " ".join(str(x) for x in spawned["argv"])
    # Detached, so it outlives the shell AND the turn that spawned it.
    assert spawned["kw"]["start_new_session"] is True


def test_await_log_lives_outside_the_worktree(monkeypatch, tmp_path):
    """These logs must never be committed with the topic's work."""
    cli = _load()
    monkeypatch.setenv("HOME", str(tmp_path))
    path = cli._await_log_path("run-1")
    assert path.startswith(str(tmp_path))
    assert path.endswith("run-1.log")


def test_await_child_reports_exit_code_and_output_tail(monkeypatch, tmp_path):
    cli = _load()
    log = str(tmp_path / "run.log")
    reported: dict = {}
    monkeypatch.setattr(
        cli, "_await_report", lambda task_id, **kw: reported.update(id=task_id, **kw)
    )

    cli._await_child("task-9", "echo 'hello from the build'; exit 7", log, 30)

    assert reported["id"] == "task-9"
    assert reported["exit_code"] == 7
    assert "hello from the build" in reported["tail"]
    assert reported["duration_s"] >= 0
    # The full output is on disk for the agent to go read.
    assert "hello from the build" in open(log, encoding="utf-8").read()


def test_await_child_kills_and_reports_124_on_timeout(monkeypatch, tmp_path):
    cli = _load()
    log = str(tmp_path / "run.log")
    reported: dict = {}
    monkeypatch.setattr(
        cli, "_await_report", lambda task_id, **kw: reported.update(**kw)
    )

    cli._await_child("task-9", "sleep 30", log, 1)

    assert reported["exit_code"] == 124
    assert reported["duration_s"] < 15  # killed at the ceiling, not waited out


def test_await_child_reports_even_when_the_command_cannot_run(monkeypatch, tmp_path):
    """A child that dies quietly is a topic that never wakes up — the exact bug
    this path exists to remove. Report something, always."""
    cli = _load()
    log = str(tmp_path / "run.log")
    reported: dict = {}
    monkeypatch.setattr(
        cli, "_await_report", lambda task_id, **kw: reported.update(**kw)
    )

    cli._await_child("task-9", "definitely-not-a-real-command-xyz", log, 30)

    assert reported["exit_code"] != 0
    assert reported["tail"]


def test_await_report_retries_before_giving_up(monkeypatch, tmp_path):
    cli = _load()
    monkeypatch.setattr(cli, "_AWAIT_RETRY_DELAYS", (0, 0, 0))
    attempts = []

    def flaky(req, timeout=None):
        attempts.append(req)
        if len(attempts) < 3:
            raise OSError("backend restarting")

        class _R:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    monkeypatch.setattr(cli.urllib.request, "urlopen", flaky)
    monkeypatch.setenv("CHEESE_TOPIC", "topic-1")
    monkeypatch.setenv("CHEESE_AWAIT_TOKEN", "wake-tok")

    cli._await_report("task-9", exit_code=0, tail="ok", duration_s=1.0)

    assert len(attempts) == 3
    assert attempts[-1].get_header("X-cheese-token") == "wake-tok"


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


def test_await_log_goes_where_the_platform_points_it(monkeypatch, tmp_path):
    """The log of a multi-hour command has to outlive the container that ran it,
    so the provider hands the CLI a path inside the host-backed session mount."""
    cli = _load()
    monkeypatch.setenv("CHEESE_AWAIT_LOGS", str(tmp_path / "cheese-await"))
    path = Path(cli._await_log_path("1754900000-42"))
    assert path.parent == tmp_path / "cheese-await"
    assert path.parent.is_dir()  # created, so the child can open the file


def test_await_log_finds_the_session_mount_on_its_own(monkeypatch, tmp_path):
    """A container from before the env var was added still gets the durable spot:
    ~/.claude IS the mount."""
    cli = _load()
    monkeypatch.delenv("CHEESE_AWAIT_LOGS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    logs = Path(cli._await_log_path("r")).parent
    assert logs == tmp_path / ".claude" / "cheese-await"


def test_await_log_falls_back_when_there_is_no_session_mount(monkeypatch, tmp_path):
    """Outside a topic container there is nothing durable to write to — run the
    command anyway rather than refusing over where its log lands."""
    cli = _load()
    monkeypatch.delenv("CHEESE_AWAIT_LOGS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path(cli._await_log_path("r")).parent == tmp_path / ".cheese" / "await"


def _is_subparsers(action):
    import argparse

    return isinstance(action, argparse._SubParsersAction)


def _subparsers_or_empty(parser):
    try:
        return _subparsers(parser)
    except AssertionError:
        return []
