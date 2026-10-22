"""`cheese transcript` turns this conversation's records into a local file.

The projection itself is the server's business (`test_session_record_views`):
what a row means is decided in one place, and the CLI only pages it, cuts it to
a range, writes it out and prints the digest. Those four things are what these
tests hold — including that a subagent's steps do not become the turn's own.
"""

import importlib.util
import json
import urllib.parse
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[2] / "sandbox/cheese"


def cheese():
    """The shipped CLI, loaded the way the device loads it."""
    loader = SourceFileLoader("cheese_cli_transcript", str(CLI))
    spec = importlib.util.spec_from_loader("cheese_cli_transcript", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def row(turn, at, parts, role="assistant", thread=None, subagent=False, parent=None):
    return {
        "seq": 1,
        "at": at,
        "turn": turn,
        "author": "cheese",
        "harness": "claude-code",
        "kind": "assistant",
        "role": role,
        "thread": thread,
        "subagent": subagent,
        "parent_thread": parent,
        "parts": parts,
    }


def text(value):
    return {"kind": "text", "text": value}


def tool(name, preview="", failed=False):
    return {"kind": "tool", "name": name, "preview": preview, "failed": failed}


def test_a_thread_declared_only_once_is_still_known_to_be_a_subagent():
    """Codex says a thread is a child in the record that started it and nowhere
    else, so a row of that thread says nothing on its own — the union over the
    whole read is what marks it."""
    module = cheese()
    rows = [
        {"turn": "a", "thread": "child", "parent_thread": "main"},
        {"turn": "a", "thread": "child", "parent_thread": None},
        {"turn": "a", "thread": "main", "parent_thread": None},
        {"turn": "a", "thread": "marked", "parent_thread": None, "subagent": True},
    ]
    assert module._subagent_threads(rows) == {"child", "marked"}


def test_the_read_asks_for_the_normalized_shape_and_the_filters(monkeypatch):
    """The file is only greppable because the server hands back one shape. A CLI
    that stopped asking for `lines` would write raw records and nothing here
    would notice — so the request itself is pinned."""
    module = cheese()
    asked = []

    def fake(method, path, *args, **kwargs):
        asked.append(path)
        return {"data": {"items": [], "next": None}}

    monkeypatch.setattr(module, "_call", fake)
    module._all_rows(
        "topic",
        module._transcript_params(
            module.build_parser().parse_args(
                [
                    "transcript",
                    "--turn",
                    "11111111-1111-1111-1111-111111111111",
                    "--grep",
                    "红的",
                    "--author",
                    "cheese",
                ]
            )
        ),
    )
    assert asked
    query = urllib.parse.parse_qs(asked[0].split("?", 1)[1])
    assert query["lines"] == ["true"]
    assert query["turn_id"] == ["11111111-1111-1111-1111-111111111111"]
    assert query["q"] == ["红的"]
    assert query["author"] == ["cheese"]


def test_the_latest_turns_are_kept_whole():
    rows = [
        row("a", "2026-10-22T08:00:00+00:00", [text("one")]),
        row("a", "2026-10-22T08:01:00+00:00", [text("two")]),
        row("b", "2026-10-22T08:02:00+00:00", [text("three")]),
        row("c", "2026-10-22T08:03:00+00:00", [text("four")]),
    ]
    kept = cheese()._latest_turns(rows, 2)
    assert [entry["turn"] for entry in kept] == ["b", "c"]


def test_a_subagents_steps_are_not_the_turns_own():
    """Both ways a row says «a subagent wrote this» count: the record's own
    mark (Claude Code, pi) and a child thread declared in another record of the
    same read (Codex)."""
    module = cheese()
    rows = [
        row("a", "2026-10-22T08:00:00+00:00", [tool("Read", "读配置")]),
        # Marked where it stands: what a Claude Code or pi subagent writes.
        row(
            "a",
            "2026-10-22T08:00:30+00:00",
            [tool("Grep", "找调用点")],
            thread="child",
            subagent=True,
        ),
        # Only its thread id here; the parent link is in another row.
        row(
            "a",
            "2026-10-22T08:00:40+00:00",
            [tool("Edit", "改配置")],
            thread="codex-child",
        ),
        row("a", "2026-10-22T08:01:00+00:00", [text("改完了")]),
        {
            **row("a", "2026-10-22T08:00:00+00:00", [], thread="codex-child"),
            "parent_thread": "a",
            "kind": "thread/started",
        },
    ]
    line = module._digest_lines(rows)[0]
    assert "1 步" in line
    assert "Read" in line
    assert "Grep" not in line
    assert "Edit" not in line
    assert "改完了" in line


def test_the_digest_says_whose_words_they_were_and_whether_it_failed():
    module = cheese()
    rows = [
        row("a", "2026-10-22T08:00:00+00:00", [text("把测试修好")], role="user"),
        row("a", "2026-10-22T08:00:10+00:00", [tool("Bash", "跑测试", True)]),
        row("a", "2026-10-22T08:00:20+00:00", [text("还是红的")]),
    ]
    line = module._digest_lines(rows)[0]
    assert line.startswith("[08:00] a ")
    assert "问「把测试修好」" in line
    assert "Bash(跑测试)" in line
    assert "还是红的" in line
    assert "有失败" in line


def test_a_record_that_belongs_to_no_turn_takes_no_line():
    """A session's own startup and command-lifecycle records belong to no turn.
    They carry no words and no steps, so a line for each would be a column of
    timestamps a reader takes for missing content."""
    module = cheese()
    rows = [
        row(None, "2026-10-22T08:00:00+00:00", []),
        {"turn": None, "at": "2026-10-22T08:00:01+00:00", "parts": [], "role": None},
        row("a", "2026-10-22T08:01:00+00:00", [text("做完了")]),
    ]
    assert module._digest_lines(rows) == ["[08:01] a · 0 步 · 做完了"]


def test_every_row_is_paged_until_the_cursor_stops(monkeypatch):
    module = cheese()
    pages = {
        None: ([{"seq": 1}, {"seq": 2}], "c1"),
        "c1": ([{"seq": 3}], None),
    }
    asked = []

    def fake(method, path, *args, **kwargs):
        assert method == "GET"
        cursor = None if "after=" not in path else path.split("after=")[1].split("&")[0]
        asked.append(cursor)
        items, following = pages[cursor]
        return {"data": {"items": items, "next": following}}

    monkeypatch.setattr(module, "_call", fake)
    assert [entry["seq"] for entry in module._all_rows("topic", {})] == [1, 2, 3]
    assert asked == [None, "c1"]


def test_recent_rows_asks_from_the_newest_end(monkeypatch):
    """A long conversation must not be walked from the beginning to find its
    last few turns."""
    module = cheese()
    seen = []
    pages = {
        None: (
            [
                row("b", "2026-10-22T08:02:00+00:00", [text("new")]),
                row("c", "2026-10-22T08:03:00+00:00", [text("newest")]),
            ],
            "older",
        ),
        "older": (
            [
                row("a", "2026-10-22T08:00:00+00:00", [text("old")]),
                row("b", "2026-10-22T08:01:00+00:00", [text("older")]),
            ],
            None,
        ),
    }

    def fake(method, path, *args, **kwargs):
        assert "newest=true" in path
        cursor = (
            None if "before=" not in path else path.split("before=")[1].split("&")[0]
        )
        seen.append(cursor)
        items, following = pages[cursor]
        return {"data": {"items": items, "next": following}}

    monkeypatch.setattr(module, "_call", fake)
    rows = module._recent_rows("topic", {}, 2)
    assert seen == [None, "older"]
    # Turn b straddles the page boundary; both of its rows are kept, and the
    # older turn a is not.
    assert [entry["turn"] for entry in rows] == ["b", "b", "c"]


def test_the_default_landing_is_outside_the_checkout(monkeypatch, tmp_path):
    module = cheese()
    monkeypatch.setattr(module, "TOPIC", "topic-1")
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        module,
        "_call",
        lambda *args, **kwargs: {
            "data": {
                "items": [row("a", "2026-10-22T08:00:00+00:00", [text("hi")])],
                "next": None,
            }
        },
    )
    module._transcript(
        module.build_parser().parse_args(
            ["transcript", "--out", str(tmp_path / "t.jsonl")]
        )
    )
    written = (tmp_path / "t.jsonl").read_text(encoding="utf-8")
    assert json.loads(written)["turn"] == "a"

    printed = []
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kwargs: printed.append(args[0])
    )
    module._transcript(module.build_parser().parse_args(["transcript"]))
    assert printed[0].startswith(
        str(tmp_path / "attachments" / "transcript" / "topic-1.jsonl")
    )
    assert "1 条" in printed[0]
    assert "1 轮" in printed[0]


def test_digest_does_not_write_a_file(monkeypatch, tmp_path):
    module = cheese()
    monkeypatch.setattr(module, "TOPIC", "topic-1")
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        module,
        "_call",
        lambda *args, **kwargs: {
            "data": {
                "items": [row("a", "2026-10-22T08:00:00+00:00", [text("hi")])],
                "next": None,
            }
        },
    )
    printed = []
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kwargs: printed.append(args[0])
    )
    module._transcript(module.build_parser().parse_args(["transcript", "--digest"]))
    assert printed == ["[08:00] a · 0 步 · hi"]
    assert not (tmp_path / "attachments" / "transcript").exists()


@pytest.mark.parametrize(
    ("size", "shown"),
    [(12, "12 B"), (2048, "2 KB"), (3 * 1024 * 1024, "3.0 MB")],
)
def test_a_size_reads_as_a_size(size, shown):
    assert cheese()._file_size(size) == shown
