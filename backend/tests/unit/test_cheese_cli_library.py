"""`cheese library`: 芝士 自己去取一份用户给过这个项目的资料。

随消息发来的资料由平台放在这个会话自己 home 的 `attachments/` 下。没跟着这条消息
来的那些——「上周那份预算表」——它得自己取,而且取下来要落在**同一个位置**:不管一
份资料是被挑进消息的还是自己取的,都在 `~/attachments/library/<名字>`,不然提示词
里那个地址和它手上那个文件就不是一回事。

落点不在工作目录里,这是硬的那一半:工作目录是被托管的那个检出,资料库那一份写进
去就是别人仓库里一个他没有加过的未跟踪文件(结论 49,不变量 I21b)。
"""

import importlib.util
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
    """默认落点是 `~/attachments/library/<名字>`,而不是工作目录下的一个路径。

    工作目录另设一处,而且这里让它不等于 home:两者相等的话,「没写进工作目录」这
    句话是靠不住的——一个相对路径也会落在同一个地方,断言照样绿。
    """
    cli = _load()
    monkeypatch.setattr(cli, "PROJECT", _PROJECT)
    monkeypatch.setattr(cli, "TOPIC", _TOPIC)
    asked: list[tuple[str, str, str | None]] = []

    def _raw(method, path, data, out=None):
        asked.append((method, path, out))
        assert out is not None
        Path(out).write_bytes(b"xlsx-bytes")

    home, workdir = tmp_path / "home", tmp_path / "room"
    workdir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "_raw_request", _raw)
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "library", "get", "预算表.xlsx"])
    cli.main()

    # 默认位置和随消息发来的那一份一致,而且目录是它自己建的。
    landed = home / "attachments" / "library" / "预算表.xlsx"
    assert landed.read_bytes() == b"xlsx-bytes"
    assert list(workdir.iterdir()) == [], "工作目录是被托管的检出,一个字节都不该多"
    method, path, out = asked[0]
    assert method == "GET"
    assert f"/projects/{_PROJECT}/library/raw" in path
    assert out == str(landed)


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
