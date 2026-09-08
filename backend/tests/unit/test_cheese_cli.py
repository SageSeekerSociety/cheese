"""cheese CLI raw-api escape hatch (fusion-design §5.2): arg parsing + wiring.

The `cheese` script has no .py extension (it's mounted into the sandbox as a
bare executable), so it's loaded by path.
"""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def test_artifact_publishes_the_local_file_from_a_subdirectory(monkeypatch, tmp_path):
    cli = _load()
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "report.html").write_text("<h1>Published result</h1>")
    monkeypatch.chdir(folder)
    monkeypatch.setenv("CHEESE_WORKTREE_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "artifact", "report.html"])
    calls = []
    monkeypatch.setattr(cli, "_call", lambda *args: calls.append(args))

    cli.main()

    assert calls == [
        (
            "POST",
            "/topics/room/artifact",
            {
                "path": "site/report.html",
                "as": "html",
                "content": "<h1>Published result</h1>",
            },
        )
    ]


@pytest.mark.parametrize("location", ["/api/topics/room/app/", "/login", None])
def test_serve_registers_the_mount_only_when_the_app_redirects_to_it(
    monkeypatch, location
):
    cli = _load()

    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302 if location else 200)
            if location:
                self.send_header("Location", location)
            self.end_headers()

        def log_message(self, *_args):
            pass

    app = ThreadingHTTPServer(("127.0.0.1", 0), App)
    thread = threading.Thread(target=app.serve_forever)
    thread.start()
    port = app.server_address[1]
    monkeypatch.setenv("CHEESE_APP_BASE", "/api/topics/room/app/")
    monkeypatch.setenv("CHEESE_PREVIEW_UP", "/preview-up")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "serve", str(port)])
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(cli, "_call", lambda *args: {})
    try:
        cli.main()
        mount = "/api/topics/room/app" if location == "/api/topics/room/app/" else ""
        assert calls == [["sh", "/preview-up", str(port), mount]]
    finally:
        app.shutdown()
        app.server_close()
        thread.join()


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
    sent: list[dict] = []
    monkeypatch.setattr(
        cli, "_call", lambda m, p, d=None: sent.append({"p": p, "d": d})
    )
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
        ],
    )

    cli.main()

    [call] = sent
    assert call["p"] == "/topics/t-1/accept-card"
    assert call["d"]["change_subject"] == "fix(accept): require a commit subject"


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


def _run_gh_token(cli, monkeypatch, permissions: str) -> None:
    monkeypatch.setattr(
        cli.urllib.request,
        "urlopen",
        lambda _req, timeout=None: _FakeHTTPResponse(
            {
                "data": {
                    "token": "ghs_x",
                    "repo": "acme/widgets",
                    "expires_at": "2026-08-12T10:00:00Z",
                    "permissions": permissions,
                }
            }
        ),
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "gh-token"])
    cli.main()


def test_gh_token_advertises_every_permission_it_actually_has(monkeypatch, capsys):
    """The whole point of widening the token: the agent has to LEARN it can
    read an issue, or it goes on asking a human to paste the body in."""
    cli = _load()
    _run_gh_token(
        cli,
        monkeypatch,
        "actions: read, checks: read, contents: read, issues: read, "
        "metadata: read, pull_requests: read",
    )

    out, err = capsys.readouterr()
    assert out.strip() == "ghs_x"  # stdout stays token-only for $(...)
    assert "repos/acme/widgets/issues/<n>" in err
    assert "repos/acme/widgets/pulls/<n>" in err
    assert "repos/acme/widgets/contents/<path>" in err


def test_gh_token_spells_out_pushing_and_opening_a_pr_when_it_may(monkeypatch, capsys):
    """Same lesson one step further along. Reading what it may do is only half
    the job — an agent that can push and open its own PR but was never shown
    the two commands hands the last step back to a human, which is exactly the
    stall the read-only token used to cause."""
    cli = _load()
    _run_gh_token(
        cli,
        monkeypatch,
        "actions: read, checks: read, contents: write, metadata: read, "
        "pull_requests: write, workflows: write",
    )

    _out, err = capsys.readouterr()
    assert "git push https://x-access-token:$GH_TOKEN@github.com/acme/widgets" in err
    assert "gh api repos/acme/widgets/pulls -f head=" in err


def test_gh_token_does_not_promise_what_it_was_not_granted(monkeypatch, capsys):
    """An advertised recipe that 403s is worse than no recipe — it burns a turn
    and teaches the agent the wrong lesson about what it may do. A read-level
    grant is one of those: `contents: read` must not produce a push recipe."""
    cli = _load()
    _run_gh_token(
        cli, monkeypatch, "actions: read, checks: read, contents: read, metadata: read"
    )

    _out, err = capsys.readouterr()
    assert "issues/<n>" not in err
    assert "pulls/<n>" not in err
    assert "git push" not in err
    assert "check-runs" in err  # what it CAN do is still spelled out
    assert "contents: read" in err


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
