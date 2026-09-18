"""`cheese library`: 芝士 自己去取一份用户给过这个项目的资料。

随消息发来的资料已经在工作目录的 `library/` 下了。没跟着这条消息来的那些——
「上周那份预算表」——它得自己取,而且取下来要落在**同一个位置**:不管一份资料是被
挑进消息的还是自己取的,路径都是 `library/<名字>`,不然提示词里那个地址和它手上
那个文件就不是一回事。
"""

import importlib.util
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
_PROJECT = "22222222-2222-4222-8222-222222222222"
_TOPIC = "11111111-1111-4111-8111-111111111111"


def _load():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_ls_lists_what_the_project_was_given(monkeypatch, capsys):
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", _PROJECT)
    monkeypatch.setattr(cli, "TOPIC", _TOPIC)
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *a, **k: {
            "data": {
                "data": [
                    {"path": "预算表.xlsx", "bytes": 2048, "modified": 1758000000},
                    {"path": "预算表(2).xlsx", "bytes": 120, "modified": 1757000000},
                ],
                "total": 2,
            }
        },
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "library", "ls"])
    cli.main()

    out = capsys.readouterr().out
    assert "预算表.xlsx" in out
    assert "预算表(2).xlsx" in out


def test_ls_says_so_when_nothing_was_given(monkeypatch, capsys):
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", _PROJECT)
    monkeypatch.setattr(cli, "TOPIC", _TOPIC)
    monkeypatch.setattr(
        cli, "_call", lambda *a, **k: {"data": {"data": [], "total": 0}}
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "library", "ls"])
    cli.main()

    assert "还没有文件" in capsys.readouterr().out


def test_get_lands_where_an_attached_file_lands(monkeypatch, tmp_path, capsys):
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", _PROJECT)
    monkeypatch.setattr(cli, "TOPIC", _TOPIC)
    asked: list[tuple[str, str, str | None]] = []

    def _raw(method, path, data, out=None):
        asked.append((method, path, out))
        assert out is not None
        Path(out).write_bytes(b"xlsx-bytes")

    monkeypatch.setattr(cli, "_raw_request", _raw)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "library", "get", "预算表.xlsx"])
    cli.main()

    # 默认位置和随消息发来的那一份一致,而且目录是它自己建的。
    assert (tmp_path / "library" / "预算表.xlsx").read_bytes() == b"xlsx-bytes"
    method, path, out = asked[0]
    assert method == "GET"
    assert f"/projects/{_PROJECT}/library/raw" in path
    assert out == os.path.join("library", "预算表.xlsx")


def test_get_can_write_somewhere_else(monkeypatch, tmp_path):
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", _PROJECT)
    monkeypatch.setattr(cli, "TOPIC", _TOPIC)
    monkeypatch.setattr(
        cli, "_raw_request", lambda m, p, d, out=None: Path(out).write_bytes(b"x")
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["cheese", "library", "get", "预算表.xlsx", "--out", "工作/表.xlsx"],
    )
    cli.main()

    assert (tmp_path / "工作" / "表.xlsx").read_bytes() == b"x"
