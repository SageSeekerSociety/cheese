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
