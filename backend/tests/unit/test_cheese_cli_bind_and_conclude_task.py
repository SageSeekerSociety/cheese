"""`cheese split` 之后还有两步,而 CLI 是唯一会告诉人这件事的地方。

派活不再起容器,所以 split 返回之后这条活是**没人做**的。分身要调用方自己起,起完
要 `cheese bind` 认领。第二步漏掉不会报错——活看着没人做,分身干的每件事都记在房间
头上——所以 split 的输出必须把剩下两步写出来,而不只是说"已派出"。
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"

_ROOM = "11111111-1111-4111-8111-111111111111"
_TASK = "33333333-3333-4333-8333-333333333333"


def _load():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run(monkeypatch, argv: list[str], data: dict) -> list[tuple[str, str, dict]]:
    cli = _load()
    calls: list[tuple[str, str, dict]] = []

    def _call(method, path, body=None, **_):
        calls.append((method, path, body or {}))
        return {"data": data}

    monkeypatch.setattr(cli, "TOPIC", _ROOM)
    monkeypatch.setattr(cli, "_call", _call)
    monkeypatch.setattr(cli.sys, "argv", ["cheese", *argv])
    cli.main()
    return calls


def test_split_says_the_work_has_nobody_on_it_yet(monkeypatch, capsys):
    _run(
        monkeypatch,
        ["split", "查一下分页", "--brief", "干这个"],
        {"id": _TASK, "title": "查一下分页"},
    )
    out = capsys.readouterr().out

    assert "还没人做" in out
    # 剩下两步都要能照着做，包括那条带 id 的命令。
    assert f"cheese bind {_TASK}" in out


def test_bind_posts_the_worker_id_to_the_thread(monkeypatch, capsys):
    calls = _run(
        monkeypatch,
        ["bind", _TASK, "worker-1"],
        {"id": _TASK, "title": "查一下分页", "subagent_id": "worker-1"},
    )

    assert calls == [
        ("POST", f"/topics/{_ROOM}/tasks/{_TASK}/bind", {"agent_id": "worker-1"})
    ]
    assert "worker-1" in capsys.readouterr().out


def test_conclude_task_names_the_thread_it_is_about(monkeypatch, capsys):
    """URL 里带 task id：收的是**某一条活**，不是房间自己。默认不带结论——分身
    停下时平台已经把它的话写在卡上了。"""
    calls = _run(
        monkeypatch,
        ["conclude-task", _TASK],
        {"id": _TASK, "title": "查一下分页", "conclusion": "分页改成 cursor"},
    )

    assert calls == [
        ("POST", f"/topics/{_ROOM}/tasks/{_TASK}/conclude", {"conclusion": ""})
    ]
    assert "分页改成 cursor" in capsys.readouterr().out


def test_conclude_task_can_overwrite_what_the_worker_left(monkeypatch):
    """分身最后那句话是个半截时，房间说了算。"""
    calls = _run(
        monkeypatch,
        ["conclude-task", _TASK, "--conclusion", "分页改成 cursor，旧接口没动"],
        {
            "id": _TASK,
            "title": "查一下分页",
            "conclusion": "分页改成 cursor，旧接口没动",
        },
    )

    assert calls[0][2] == {"conclusion": "分页改成 cursor，旧接口没动"}


def test_conclude_task_declares_actual_contributors(monkeypatch):
    calls = _run(
        monkeypatch,
        [
            "conclude-task",
            _TASK,
            "--reported-by",
            "alice",
            "--contributor",
            "bob",
            "--contributor",
            "carol",
        ],
        {"id": _TASK, "title": "Fix pagination"},
    )
    assert calls[0][2] == {
        "conclusion": "",
        "reporter_handle": "alice",
        "contributor_handles": ["bob", "carol"],
    }
