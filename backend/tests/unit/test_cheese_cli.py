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
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(cli, "_call", lambda *args, **_: calls.append(args) or {})

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


def test_show_after_a_show_says_which_version_the_edit_was_made_on(
    monkeypatch, tmp_path
):
    """第二次 show 带上第一次写下的版本：期间有人在编辑器里存过，平台才认得出来。"""
    cli = _load()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHEESE_WORKTREE_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "TOPIC", "room")
    bodies = []
    monkeypatch.setattr(cli, "_call", lambda *args, **_: bodies.append(args[2]) or {})
    (tmp_path / "a.md").write_text("one")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "show", "a.md"])
    cli.main()
    (tmp_path / "a.md").write_text("two")
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "show", "a.md", "--note", "改了第二段"]
    )
    cli.main()

    assert "base_version" not in bodies[0]
    assert bodies[1]["base_version"] == cli.hashlib.sha256(b"one").hexdigest()[:16]
    assert bodies[1]["note"] == "改了第二段"


def test_show_refused_for_a_stale_base_tells_the_agent_to_pull(
    monkeypatch, tmp_path, capsys
):
    cli = _load()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHEESE_WORKTREE_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(
        cli, "_call", lambda *a, **k: {"ok": False, "status": 409, "error": {}}
    )
    (tmp_path / "a.md").write_text("mine")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "show", "a.md"])

    with pytest.raises(SystemExit) as exited:
        cli.main()

    assert exited.value.code == 1
    assert "cheese pull a.md" in capsys.readouterr().err


def test_serve_declares_only_the_port_and_registers_the_app(monkeypatch):
    cli = _load()
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "serve", "5173", "Vue dev server", "--no-wait"]
    )
    calls = []
    api_calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(cli, "_call", lambda *args: api_calls.append(args) or {})
    cli.main()
    assert calls == [["sh", "/preview-up", "5173"]]
    assert api_calls == [
        ("POST", "/topics/room/shown", {"path": "Vue dev server", "as": "app"})
    ]


def test_serve_waits_for_the_app_before_declaring(monkeypatch):
    """默认先探通:应用还没起来时,报端口之前先等到它应答。"""
    cli = _load()
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "serve", "5173", "Vue dev server"])
    waited = []
    monkeypatch.setattr(
        cli,
        "_wait_for_app",
        lambda port, timeout: waited.append((port, timeout)) or 200,
    )
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(cli, "_call", lambda *args, **_: {})

    cli.main()

    assert waited == [(5173, cli._SERVE_WAIT_DEFAULT_S)]
    assert calls == [["sh", "/preview-up", "5173"]]


def test_serve_no_wait_skips_the_probe(monkeypatch):
    """--no-wait(和 --wait 0)不探,报完就走,给不想等的调用方留一条路。"""
    cli = _load()
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "serve", "5173", "--wait", "0"])

    def _boom(*_a, **_k):
        raise AssertionError("--wait 0 不该探")

    monkeypatch.setattr(cli, "_wait_for_app", _boom)
    monkeypatch.setattr(cli.subprocess, "call", lambda args: 0)
    monkeypatch.setattr(cli, "_call", lambda *args, **_: {})

    cli.main()


def test_serve_warns_but_still_declares_when_nothing_answers(monkeypatch, capsys):
    """等超了照样报:提醒一句就够,平台那一敲仍是权威,不该把 agent 卡在这。"""
    cli = _load()
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "serve", "5173"])
    monkeypatch.setattr(cli, "_wait_for_app", lambda port, timeout: None)
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(cli, "_call", lambda *args, **_: {})

    cli.main()

    assert calls == [["sh", "/preview-up", "5173"]]
    err = capsys.readouterr().err
    assert "5173" in err and "没有应答" in err


def test_serve_with_cmd_starts_detached_app_and_records_launch_meta(
    monkeypatch, tmp_path
):
    """`cheese serve --cmd` starts a silent app, records its launch spec locally
    and sends it with `/shown` so the platform can restart it without an LLM turn."""
    cli = _load()
    task_id = "11111111-2222-3333-4444-555555555555"
    home = tmp_path / "home"
    task_dir = home / ".cheese" / "tasks" / task_id / "frontend"
    task_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.chdir(task_dir)
    started: list[tuple[str, str, int]] = []
    monkeypatch.setattr(cli, "_probe_app", lambda port: None)
    monkeypatch.setattr(
        cli,
        "_start_detached_serve_cmd",
        lambda cmd, cwd, port: started.append((cmd, cwd, port)),
    )
    monkeypatch.setattr(cli, "_wait_for_app", lambda port, timeout: 200)
    monkeypatch.setattr(cli.subprocess, "call", lambda args: 0)
    api_calls: list[tuple] = []
    monkeypatch.setattr(cli, "_call", lambda *args: api_calls.append(args) or {})
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "cheese",
            "serve",
            "4173",
            "Vite preview",
            "--cmd",
            "./node_modules/.bin/vite preview --port 4173 --strictPort",
        ],
    )

    cli.main()

    assert started == [
        (
            "./node_modules/.bin/vite preview --port 4173 --strictPort",
            str(task_dir.resolve()),
            4173,
        )
    ]
    assert len(api_calls) == 1
    method, path, body = api_calls[0]
    assert (method, path) == ("POST", "/topics/room/shown")
    assert body["path"] == "Vite preview" and body["as"] == "app"
    serve = body["serve"]
    assert serve == {
        "port": 4173,
        "command": "./node_modules/.bin/vite preview --port 4173 --strictPort",
        "cwd": str(task_dir.resolve()),
        "home": str(home.resolve()),
        "task_id": task_id,
    }
    saved = cli.json.loads((home / ".cheese/cheese-preview.json").read_text())
    assert saved == serve


def test_serve_restore_opens_missing_worktree_relocates_home_and_restarts_app(
    monkeypatch, tmp_path
):
    """`cheese serve --restore` in a fresh sandbox opens the task checkout,
    relocates paths from the old sandbox home, starts the command and brings
    the preview tunnel up."""
    cli = _load()
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    old_home = tmp_path / "old-home"
    new_home = tmp_path / "new-home"
    (new_home / ".cheese").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(new_home))
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")

    opened_tasks: list[str] = []

    def fake_task_worktree(tid: str, *, reinstall: bool = False):
        opened_tasks.append(tid)
        tdir = new_home / ".cheese" / "tasks" / tid
        (tdir / ".git").mkdir(parents=True, exist_ok=True)
        (tdir / "web").mkdir(parents=True, exist_ok=True)
        return tdir

    monkeypatch.setattr(cli, "_task_worktree", fake_task_worktree)
    up_calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: up_calls.append(args) or 0)
    monkeypatch.setattr(cli, "_probe_app", lambda port: None)
    started: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        cli,
        "_start_detached_serve_cmd",
        lambda cmd, cwd, port: started.append((cmd, cwd, port)),
    )
    monkeypatch.setattr(cli, "_wait_for_app", lambda port, timeout: 200)

    spec = {
        "port": 5173,
        "command": (
            f"node {old_home}/.cheese/tasks/{task_id}/web/server.js "
            f"--static {old_home}-other/keep --port 5173"
        ),
        "cwd": f"{old_home}/.cheese/tasks/{task_id}/web",
        "home": str(old_home),
        "task_id": task_id,
    }
    rc = cli._restore_served_app(cli.json.dumps(spec), 10.0)
    assert rc == 0
    assert opened_tasks == [task_id]
    assert up_calls == [["sh", "/preview-up", "5173"]]
    resolved_new = str(new_home.resolve())
    assert started == [
        (
            f"node {resolved_new}/.cheese/tasks/{task_id}/web/server.js "
            f"--static {old_home}-other/keep --port 5173",
            f"{resolved_new}/.cheese/tasks/{task_id}/web",
            5173,
        )
    ]
    # A non-UUID task_id (e.g. path traversal) is refused before touching the disk.
    bad_spec = {**spec, "task_id": "../checkout"}
    assert cli._restore_served_app(cli.json.dumps(bad_spec), 10.0) == 2


@pytest.mark.skipif(
    not Path("/proc/net/tcp").exists(),
    reason="/proc/net/tcp process inspection is Linux-specific",
)
def test_detect_listening_process_finds_real_child_command_and_cwd(tmp_path):
    """When an agent starts a server in the background and calls `cheese serve`
    without `--cmd`, `_detect_listening_process` reads its command line and
    working directory from `/proc`, normalizing paths under `cwd` to `./...`."""
    import os
    import socket
    import subprocess
    import sys
    import time

    cli = _load()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    script = tmp_path / "run_srv.py"
    script.write_text(
        "import http.server, sys\n"
        "srv = http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), http.server.BaseHTTPRequestHandler)\n"
        "srv.serve_forever()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, str(script), str(port)],
        cwd=str(tmp_path),
    )
    try:
        deadline = time.monotonic() + 5.0
        while cli._probe_app(port) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        cmd, cwd = cli._detect_listening_process(port)
        assert cmd is not None and cwd is not None
        assert "./run_srv.py" in cmd and str(port) in cmd
        assert os.path.realpath(cwd) == os.path.realpath(tmp_path)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_wait_for_app_probes_a_real_server_and_a_dead_port():
    """_wait_for_app 走真网络:有人在听返回状态码,没人听就等满超时返回 None。"""
    import http.server
    import socket
    import threading

    cli = _load()

    class _Handler(http.server.BaseHTTPRequestHandler):
        seen: list[str] = []

        def do_GET(self):  # http.server's own spelling
            type(self).seen.append(self.headers.get("Accept", ""))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html></html>")

        def log_message(self, *_a):  # 安静点,测试输出不掺日志
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert cli._wait_for_app(port, 5.0) == 200
        # 探的是浏览器式的请求:Accept 里带 text/html。
        assert any("text/html" in value for value in _Handler.seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    # 一个没人听的端口:拿到连接被拒,不该瞎等满整段时间。
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    start = cli.monotonic()
    assert cli._wait_for_app(dead_port, 0.6) is None
    assert cli.monotonic() - start < 5.0


def test_wait_for_app_returns_at_once_when_the_app_answers_non_2xx():
    """应答了就算到了:4xx/5xx 也是「在听、能应答」,不该把整段等待熬完再报。

    404 的根路径(auth 门、SPA 挂子路径、纯 API 服务)是最常见的一种「起来了但
    根路径不对」——caller 收到 4xx 就当场照报,探活这里再接着敲就纯属空等。"""
    import http.server
    import threading

    cli = _load()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # http.server's own spelling
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")

        def log_message(self, *_a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        start = cli.monotonic()
        assert cli._wait_for_app(port, 30.0) == 404
        # 立刻返回:远远小于 30 秒的预算,而不是熬满。
        assert cli.monotonic() - start < 5.0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_probe_app_survives_a_non_http_listener_on_the_port():
    """端口上坐的是不讲 HTTP 的服务(SSH 先打招呼这种):探活返回 None,不抛。

    http.client 对非状态行的首行抛 BadStatusLine(HTTPException,不是 OSError),
    漏掉它会让 `cheese serve` 在一个被占的端口上直接崩掉。"""
    import socket
    import threading

    cli = _load()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(5)
    stop = threading.Event()

    def _greet_banner():
        # 连上就发一串非 HTTP 的 banner,像 SSH/Postgres 那样先开口。
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                conn.sendall(b"SSH-2.0-OpenSSH_9.0\r\n")
            except OSError:
                pass
            conn.close()

    thread = threading.Thread(target=_greet_banner, daemon=True)
    thread.start()
    try:
        # 不抛异常,如实返回「敲不通」。
        assert cli._probe_app(port) is None
        assert cli._wait_for_app(port, 0.6) is None
    finally:
        stop.set()
        server.close()
        thread.join()


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
        seen["token"] = req.get_header("Authorization").removeprefix("Bearer ")
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


def test_format_status_says_which_layer_and_when_it_comes_back():
    """芝士要的是「这一层用完我会不会被拒绝、几时恢复」，所以剩余之外还要带上限、
    恢复时间，以及不会自动恢复的那一层项目预算。"""
    cli = _load()
    payload = _status_payload({"status": "running", "near_ceiling": False})
    payload["platform"]["credits"] = {
        "unlimited": False,
        "remaining": 380,
        "total": 500,
        "resets_at": "11月1日 00:00",
        "project_remaining": 90,
        "layer": "plan",
    }
    out = cli._format_status(payload)
    assert "剩 380 / 上限 500 / 11月1日 00:00 恢复" in out
    assert "本项目预算还剩 90（不会自动恢复）" in out


def test_format_status_says_the_project_budget_does_not_come_back():
    """先停的是项目预算时，上面那组数就是它，而且没人「等一等」就能恢复。"""
    cli = _load()
    payload = _status_payload({"status": "running", "near_ceiling": False})
    payload["platform"]["credits"] = {
        "unlimited": False,
        "remaining": 90,
        "total": 100,
        "resets_at": None,
        "project_remaining": 90,
        "layer": "project_budget",
    }
    out = cli._format_status(payload)
    assert "剩 90 / 上限 100；这个上限不会自动恢复，要管理员调" in out
    assert "本项目预算还剩" not in out


# --- the self-describing layer -------------------------------------------
#
# AGENTS.md's rule is "prose documents only what --help cannot tell you". That
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
    """开活是会话侧的 `cheese_task(action=open)`；目录在机器上，由这一条准备。"""
    cli = _load()
    prepared = []

    def worktree(task_id, reinstall=False):
        prepared.append((task_id, reinstall))
        return tmp_path / task_id

    monkeypatch.setattr(cli, "_task_worktree", worktree)
    task = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "worktree", task])
    cli.main()
    assert prepared == [(task, False)]
    assert capsys.readouterr().out.strip() == str(tmp_path / task)


def test_checkout_brings_the_default_branch_and_keeps_what_was_changed(
    monkeypatch, tmp_path, capsys
):
    """A 支线 reads the project's code from `cheese checkout`: a copy of the
    default branch, brought up to date on the next call unless something in it
    was changed there."""
    import subprocess

    def git(cwd, *args):
        subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "trunk")
    (work / "app.py").write_text("v1\n")
    git(work, "add", ".")
    git(work, "commit", "-qm", "one")
    forge = tmp_path / "forge.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(forge))

    cli = _load()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    token = {"data": {"url": str(forge), "kind": "none", "default_branch": "trunk"}}
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kw: token)
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "checkout"])

    cli.main()
    checkout = Path(capsys.readouterr().out.strip())
    assert (checkout / "app.py").read_text() == "v1\n"

    (work / "app.py").write_text("v2\n")
    git(work, "commit", "-qam", "two")
    git(work, "push", "-q", str(forge), "trunk")
    cli.main()
    assert (checkout / "app.py").read_text() == "v2\n"

    (checkout / "app.py").write_text("trying something\n")
    (work / "app.py").write_text("v3\n")
    git(work, "commit", "-qam", "three")
    git(work, "push", "-q", str(forge), "trunk")
    cli.main()
    assert (checkout / "app.py").read_text() == "trying something\n"
