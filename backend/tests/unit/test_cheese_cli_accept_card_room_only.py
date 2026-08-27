"""`cheese accept-request` 在一条支线里当场就该拒绝,不必打出去一趟。

后端才是真正的规则(见 tests/integration/test_accept_card_room_only.py)。这里拦
一道是为了让分身在自己的沙箱里就读到「这不是你的活,去 cheese conclude」,而不是
拿回一个 HTTP 状态码自己猜。

关键的一面是**不能拦错人**:房间递卡是正常操作,而不是每个运行环境都往环境变量里
放 CHEESE_ROOM——不知道的时候必须放行,让后端去判。
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"

_ROOM = "11111111-1111-4111-8111-111111111111"
_THREAD = "22222222-2222-4222-8222-222222222222"

_ARGV = ["cheese", "accept-request", "alice", "最懂", "--subject", "fix(x): do it"]


def _load():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run(monkeypatch, *, topic: str, room: str) -> list[tuple[str, str]]:
    """跑一次 `cheese accept-request`,返回它实际打出去的请求。"""
    cli = _load()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "TOPIC", topic)
    monkeypatch.setattr(cli, "ROOM", room)
    monkeypatch.setattr(cli, "_call", lambda m, p, b=None, **kw: calls.append((m, p)))
    monkeypatch.setattr(cli.sys, "argv", list(_ARGV))
    try:
        cli.main()
    finally:
        _run.calls = calls  # type: ignore[attr-defined]
    return calls


def test_a_thread_is_refused_without_a_round_trip(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, topic=_THREAD, room=_ROOM)

    assert exc.value.code != 0
    err = capsys.readouterr().err
    # 只说"不允许"等于让分身原地打转,下一步的命令必须在错误里。
    assert "cheese conclude" in err
    # 而且一个请求都没发出去——省的就是这一趟。
    assert _run.calls == []  # type: ignore[attr-defined]


def test_a_room_still_files_its_card(monkeypatch):
    assert _run(monkeypatch, topic=_ROOM, room=_ROOM) == [
        ("POST", f"/topics/{_ROOM}/accept-card")
    ]


def test_a_runtime_that_does_not_say_which_room_lets_the_backend_decide(monkeypatch):
    """环境变量里没有 CHEESE_ROOM 时不许自作主张。

    「不知道」被读成「你是支线」的话,凡是没设这个变量的运行环境里,房间也递不出卡
    了——一道本地便利拦掉了平台唯一的交付出口。
    """
    assert _run(monkeypatch, topic=_THREAD, room="") == [
        ("POST", f"/topics/{_THREAD}/accept-card")
    ]
