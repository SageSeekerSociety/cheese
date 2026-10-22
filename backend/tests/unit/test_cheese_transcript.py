"""`cheese transcript`：本会话的原始记录落到机器上，原文不进上下文。

这条命令存在的理由只有一条：agent 要翻自己上一轮到底做了什么，而把原文拉进
上下文会把它自己撑爆（实测 5 条就一百多 KB）。所以这里守的是那条分界 ——
**stdout 只回路径和统计，原文永远走文件**；以及「要能选」：轮次、词、来源。

每一条都是真跑命令行（`main()` 走 argparse 和分派），只把打平台的那一次调用换
成桩。桩按平台上那条接口的契约答：一页 `items` 加一个 `next` 游标。
"""

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _record(sequence: int, *, turn="t1", agent="cheese", harness="claude-code"):
    return {
        "sequence": sequence,
        "at": f"2026-10-22T08:1{sequence}:00+00:00",
        "turn_id": turn,
        "agent_handle": agent,
        "harness": harness,
        "record": {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"第 {sequence} 条说的话"}],
            },
        },
    }


def _tool_call(sequence: int, name: str, arguments: dict, call: str):
    return {
        "sequence": sequence,
        "at": f"2026-10-22T08:2{sequence}:00+00:00",
        "turn_id": "t1",
        "agent_handle": "cheese",
        "harness": "claude-code",
        "record": {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": call, "name": name, "input": arguments}
                ],
            },
        },
    }


def _tool_result(sequence: int, call: str, text: str):
    return {
        "sequence": sequence,
        "at": f"2026-10-22T08:3{sequence}:00+00:00",
        "turn_id": "t1",
        "agent_handle": "cheese",
        "harness": "claude-code",
        "record": {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call, "content": text}
                ],
            },
        },
    }


#: 一页，和一个指向别处的游标 —— 分页没跟上的话第二页的记录就不在了。
FIRST_PAGE = [_record(1), _record(2)]
SECOND_PAGE = [_record(3, turn="t2")]
LONG_TEXT = "x" * 400


class Platform:
    """平台上那条接口的桩：记住问了什么，按页答。"""

    def __init__(self, pages):
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, method, path, *args, **kwargs):
        assert method == "GET", f"transcript 只读：{method} {path}"
        self.asked.append(path)
        page = self.pages[min(len(self.asked) - 1, len(self.pages) - 1)]
        return {
            "data": {
                "items": page,
                "next": None if len(self.asked) >= len(self.pages) else "cursor-2",
            }
        }


@pytest.fixture
def cli(monkeypatch, tmp_path):
    module = _load()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(module, "TOPIC", "room")
    return module


def _run(cli, *argv):
    cli.sys.argv = ["cheese", "transcript", *argv]
    cli.main()


def test_the_record_lands_in_a_file_and_stdout_carries_only_the_stats(
    cli, tmp_path, capsys
):
    """这条命令的全部意义：原文进文件，stdout 只有路径和统计。

    把一整条记录打出来就退化成「返回原文」了 —— 那正是设计里否掉的做法。
    """
    platform = Platform([FIRST_PAGE])
    cli._call = platform
    _run(cli)
    out = capsys.readouterr().out

    path = Path(out.splitlines()[0])
    assert path == (tmp_path / "attachments" / "transcript" / "room.jsonl"), (
        "默认落点必须在会话自己 home 的 attachments/ 下，不在检出目录里"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["sequence"] for line in lines] == [1, 2]
    assert "2 条记录 · 1 轮" in out
    assert "第 1 条说的话" not in out, "记录的正文不许出现在 stdout 里"


def test_it_follows_the_cursor_to_the_last_page(cli, tmp_path, capsys):
    """封存进私有桶的那几段只有沿着游标才读得到：漏一页就是丢一段。"""
    platform = Platform([FIRST_PAGE, SECOND_PAGE])
    cli._call = platform
    _run(cli)
    out = capsys.readouterr().out

    path = Path(out.splitlines()[0])
    assert [json.loads(line)["sequence"] for line in path.read_text().splitlines()] == [
        1,
        2,
        3,
    ]
    assert "3 条记录 · 2 轮" in out
    assert len(platform.asked) == 2, "第二页没读"


def test_the_filters_reach_the_platform_and_name_the_file(cli, tmp_path, capsys):
    """筛选是在平台上做的（先搜后落），不是拉回来再挑：拉回来就等于把整份读进内存。

    三次调用各带一个参数，也都得进文件名 —— 否则一次窄查会盖掉刚落的整份。
    """
    platform = Platform([[_record(1)]])
    cli._call = platform

    _run(cli, "--grep", "build log")
    grep_path = Path(capsys.readouterr().out.splitlines()[0])
    assert "q=build+log" in platform.asked[-1]
    assert grep_path.name == "room.grep-build-log.jsonl"

    _run(cli, "--turn", "8fb847ed")
    assert "turn_id=8fb847ed" in platform.asked[-1]
    assert Path(capsys.readouterr().out.splitlines()[0]).name == "room.8fb847ed.jsonl"

    _run(cli, "--author", "cheese-abc123")
    assert "author=cheese-abc123" in platform.asked[-1]
    assert (
        Path(capsys.readouterr().out.splitlines()[0]).name == "room.cheese-abc123.jsonl"
    )


def test_out_wins_over_the_default_place(cli, tmp_path, capsys):
    elsewhere = tmp_path / "elsewhere" / "翻.jsonl"
    cli._call = Platform([FIRST_PAGE])
    _run(cli, "--out", str(elsewhere))
    assert str(elsewhere) in capsys.readouterr().out
    assert elsewhere.read_text(encoding="utf-8").count("\n") == 2


def test_nothing_matched_says_so_instead_of_leaving_an_empty_file(cli, capsys):
    cli._call = Platform([[]])
    _run(cli, "--grep", "没有这个词")
    out = capsys.readouterr().out
    assert "0 条记录" in out
    assert "没有记录" in out


def test_digest_prints_an_index_and_writes_no_file(cli, tmp_path, capsys):
    """先看形状再决定要不要拉原文：索引里每一步一行，带工具名、一行结果和记录号。"""
    calls = [
        _tool_call(4, "Bash", {"command": "make", "description": "跑测试"}, "c1"),
        _tool_result(6, "c1", LONG_TEXT),
    ]
    cli._call = Platform([[*FIRST_PAGE, *calls]])
    _run(cli, "--digest")
    out = capsys.readouterr().out

    assert not (tmp_path / "attachments").exists(), "--digest 不落文件"
    assert "轮 t1" in out
    assert "Bash 跑测试 → " + LONG_TEXT[:60] + "…" in out
    # 索引里每一步都指得回原文那一条。
    assert "#4" in out and "#1" in out
    assert LONG_TEXT not in out, "digest 里放的是压过的一行，不是原文"


def test_digest_stops_at_the_line_budget_and_says_what_is_left(cli, capsys):
    """agent 没有「自己上下文还剩多少」的概念：不封顶它就自己灌自己。

    封顶之后必须说清还有多少、怎么接着取，否则封顶就成了丢数据。
    """
    many = [_record(n) for n in range(1, 60)]
    cli._call = Platform([many])
    _run(cli, "--digest", "--lines", "5")
    out = capsys.readouterr().out

    assert len(out.strip().splitlines()) <= 6, out
    assert "还有" in out and "--lines" in out


def test_a_subagents_record_is_marked_as_the_subagents(cli, capsys):
    """分身和我混在同一份记录里：digest 要说得出哪一步是分身做的，否则读的人
    会把自己没做过的事当成自己做的。"""
    # Claude Code 的分身记录多包一层 `cheese_file`，`agent_id` 在那一层上。
    from_file = {
        "sequence": 9,
        "at": "2026-10-22T08:40:00+00:00",
        "turn_id": "t1",
        "agent_handle": "cheese",
        "harness": "claude-code",
        "record": {
            "type": "cheese_file",
            "agent_id": "a1b2c3d4",
            "entry": {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "c9",
                            "name": "Read",
                            "input": {"file_path": "/work/backend/app/main.py"},
                        }
                    ],
                },
            },
        },
    }
    # 而现在这一版把它直接交上来：`agent_id` 盖在记录本身上，没有那层包装。
    # 两种都要认 —— 现场视图认的就是这两个位置。
    plain = {
        "sequence": 10,
        "at": "2026-10-22T08:41:00+00:00",
        "turn_id": "t1",
        "agent_handle": "cheese",
        "harness": "claude-code",
        "record": {
            "type": "assistant",
            "agent_id": "e5f6a7b8",
            "parent_tool_use_id": "tool-1",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "c10",
                        "name": "Bash",
                        "input": {"command": "pytest -q", "description": "跑测试"},
                    }
                ],
            },
        },
    }
    cli._call = Platform([[from_file, plain]])
    _run(cli, "--digest")
    out = capsys.readouterr().out
    assert "分身a1b2c3d4" in out
    assert "Read /work/backend/app/main.py" in out
    assert "分身e5f6a7b8" in out
    assert "Bash 跑测试" in out


def test_a_subagent_of_another_harness_is_marked_too(cli, capsys):
    """三种骨架各有各的说法，认不出来是一种静默的错：记录里明明有分身做过的事，
    digest 却把它算成主会话做的。这里按每种骨架真写下来的形状各来一条。"""
    pi = {
        "sequence": 1,
        "at": "2026-10-22T08:40:00+00:00",
        "turn_id": "t1",
        "agent_handle": "cheese",
        "harness": "pi",
        "record": {
            "subagent": {"id": "pi-agent-7"},
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "toolCall", "id": "p1", "name": "bash", "arguments": {}},
                ],
            },
        },
    }
    # Codex 只在一个线程宣布「我的父线程是谁」时说过这件事，它自己的记录只有
    # threadId，要顺着那个宣告查回来。
    codex_announce = {
        "sequence": 2,
        "at": "2026-10-22T08:41:00+00:00",
        "turn_id": "t1",
        "agent_handle": "cheese",
        "harness": "codex",
        "record": {
            "method": "thread/started",
            "params": {"thread": {"id": "child-1", "parentThreadId": "main-1"}},
        },
    }
    codex_child = {
        "sequence": 3,
        "at": "2026-10-22T08:42:00+00:00",
        "turn_id": "t1",
        "agent_handle": "cheese",
        "harness": "codex",
        "record": {
            "method": "item/completed",
            "params": {
                "threadId": "child-1",
                "item": {
                    "type": "agentMessage",
                    "id": "m1",
                    "text": "分身 codex 查到的东西",
                },
            },
        },
    }
    cli._call = Platform([[pi, codex_announce, codex_child]])
    _run(cli, "--digest")
    out = capsys.readouterr().out
    assert "分身pi-agent-7" in out, out
    assert "分身child-1" in out, out


def test_the_main_sessions_own_records_are_not_marked_as_a_subagent(cli, capsys):
    """反过来的错更坏：主会话做的事被标成分身做的，读的人就找不到是谁做的了。"""
    cli._call = Platform([[_record(1)]])
    _run(cli, "--digest")
    assert "分身" not in capsys.readouterr().out


def test_a_cursor_that_comes_back_again_ends_the_read(cli, capsys):
    """游标不前进时停下来。一直读下去会把落文件撑成无限大，而那正是这条命令
    不该做的事 —— 它写的是磁盘，没人看着它什么时候停。"""
    asked = []

    def call(_method, path, *args, **kwargs):
        asked.append(path)
        # 前两次说「同一个游标，还有更多」——不认的话就是一个死循环。第三次
        # 起答「没有了」，好让不认的那一版跑得完、红得清楚，而不是把整个套件
        # 挂在那里（CI 上没有谁会来打断它）。
        if len(asked) > 2:
            return {"data": {"items": [_record(1)], "next": None}}
        return {"data": {"items": [_record(1)], "next": "same-cursor"}}

    cli._call = call
    _run(cli)
    done = capsys.readouterr()
    assert len(asked) == 2, f"第二次拿到同一个游标就该停：{len(asked)} 次"
    assert "同一个游标" in done.err
    assert Path(done.out.splitlines()[0]).read_text().count("\n") == 2


def test_the_line_budget_counts_the_turn_heading_too(cli, capsys):
    """轮标题也是索引里的一行。只数步骤的话，每进一轮就超一行 —— 封顶封不住，
    而封顶正是这条命令存在的理由之一（agent 不知道自己上下文还剩多少）。

    末尾那行「还有 N 条」不算在预算里：它只在超了的时候出现，数它就得先知道
    要不要数它。"""
    two_turns = [
        {
            "sequence": 1,
            "at": "2026-10-22T08:11:00+00:00",
            "turn_id": "t1",
            "agent_handle": "cheese",
            "harness": "claude-code",
            "record": {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "一"}],
                },
            },
        },
        {
            "sequence": 2,
            "at": "2026-10-22T08:12:00+00:00",
            "turn_id": "t2",
            "agent_handle": "cheese",
            "harness": "claude-code",
            "record": {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "二"}],
                },
            },
        },
    ]
    cli._call = Platform([two_turns])
    _run(cli, "--digest", "--lines", "3")
    index = [
        line
        for line in capsys.readouterr().out.strip().splitlines()
        if not line.startswith("……")
    ]
    # 只数步骤时这里会打出四行（两个标题加两步），超出的那一行就是没算标题。
    assert len(index) <= 3, index
    assert index[:2] == ["轮 t1 · 1 条", "  #1 说 一"], index


def test_digest_and_a_destination_path_are_not_both_asked_for(cli):
    """`--digest` 说不落文件；两个一起给时定不了听谁的，宁可当场拒绝。"""
    with pytest.raises(SystemExit) as exited:
        cli.build_parser().parse_args(["transcript", "--digest", "--out", "x.jsonl"])
    assert exited.value.code == 2


def test_the_zero_line_budget_is_refused(cli, capsys):
    cli._call = Platform([FIRST_PAGE])
    with pytest.raises(SystemExit) as exited:
        _run(cli, "--digest", "--lines", "0")
    assert exited.value.code == 2
    assert "至少是 1" in capsys.readouterr().err
