"""Read stored conversation data through the `chat list|get|replies|search`
commands — the ones cli_worker publishes to the agent as `cheese_chat_*` tools."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.test_cheese_cli import _load


def _message(number=1, **extra):
    return {
        "id": f"00000000-0000-0000-0000-{number:012d}",
        "topic_id": "room-1",
        "kind": "message",
        "author": "alice",
        "author_type": "human",
        "created_at": "2026-09-08T00:00:00Z",
        "content": "Use the revised dataset",
        "reply_to": None,
        "reactions": [],
        **extra,
    }


def _run(monkeypatch, args, payload):
    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", "room-1")
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        return {"data": payload}

    monkeypatch.setattr(cli, "_call", request)
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "chat", *args])
    cli.main()
    return calls


def test_default_chat_is_one_bounded_page(monkeypatch, capsys):
    calls = _run(monkeypatch, ["list"], {"data": [_message()], "has_more": False})
    parsed = urlsplit(calls[0][1])
    assert calls[0][0] == "GET"
    assert parsed.path == "/topics/room-1/history"
    assert parse_qs(parsed.query)["limit"] == ["50"]
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "alice" in out and "Use the revised dataset" in out


def test_search_encodes_literal_query_and_preserves_scope(monkeypatch, capsys):
    calls = _run(
        monkeypatch,
        ["search", "报错 & 50%_", "--topic", "room-2", "--task", "card-1"],
        {"data": [], "has_more": False},
    )
    parsed = urlsplit(calls[0][1])
    assert parsed.path == "/topics/room-2/history"
    assert parse_qs(parsed.query)["q"] == ["报错 & 50%_"]
    assert parse_qs(parsed.query)["task_id"] == ["card-1"]
    assert "No messages" in capsys.readouterr().out


@pytest.mark.parametrize(
    "kind",
    [
        "message",
        "doc",
        "decision",
        "attachment",
        "event",
        "comment",
        "artifact",
        "doc_node",
        "future_kind",
    ],
)
def test_special_messages_keep_content_metadata_replies_and_reactions(
    monkeypatch, capsys, kind
):
    block = _message(
        kind=kind,
        reply_to="parent-id",
        mime_type="application/pdf",
        anchor_quote="selected paragraph",
        refs=["source-id"],
        meta={"options": ["Yes", "No"], "answered": "Yes", "detail": "full error"},
        reactions=[{"emoji": "👍", "count": 2, "authors": ["bob", "carol"]}],
    )
    _run(monkeypatch, ["get", block["id"]], block)
    out = capsys.readouterr().out
    for value in (
        kind,
        "parent-id",
        "application/pdf",
        "selected paragraph",
        "source-id",
        "answered",
        "full error",
        "👍",
        "bob",
        "carol",
    ):
        assert value in out


def test_replies_query_is_explicit_and_can_page(monkeypatch):
    calls = _run(
        monkeypatch,
        ["replies", "parent-id", "--before", "cursor-id", "--limit", "3"],
        {"data": [_message(reply_to="parent-id")], "has_more": False},
    )
    params = parse_qs(urlsplit(calls[0][1]).query)
    assert params["reply_to"] == ["parent-id"]
    assert params["before"] == ["cursor-id"]
    assert params["limit"] == ["3"]


def test_json_preserves_every_field(monkeypatch, capsys):
    block = _message(meta={"future": {"data": [1, "x"]}}, content="x" * 20000)
    _run(monkeypatch, ["get", block["id"], "--json"], block)
    assert json.loads(capsys.readouterr().out) == block


def test_long_message_can_be_read_without_losing_the_tail(monkeypatch, capsys):
    block = _message(content="a" * 20000 + "END OF MESSAGE")
    _run(monkeypatch, ["get", block["id"]], block)
    first = capsys.readouterr().out
    assert len(first) < 13000
    assert 'cheese_chat_get(message_id="' in first and "offset=12000" in first
    _run(monkeypatch, ["get", block["id"], "--offset", "12000"], block)
    assert "END OF MESSAGE" in capsys.readouterr().out


def test_page_budget_keeps_newest_messages_and_a_real_next_cursor(monkeypatch, capsys):
    blocks = [_message(i, content=f"message-{i}:" + "x" * 20000) for i in range(1, 51)]
    _run(monkeypatch, ["list"], {"data": blocks, "has_more": False})
    out = capsys.readouterr().out
    assert len(out) < 13000
    assert "message-50:" in out
    assert "cheese_chat_get(" in out
    assert "cheese_chat_list(" in out
    # The continuation must start before the oldest SHOWN message, not before
    # the server's entire page, or budget trimming would silently skip rows.
    shown = [b for b in blocks if f"message-{int(b['id'][-12:])}:" in out]
    assert f'before="{shown[0]["id"]}"' in out


@pytest.mark.parametrize(
    "args",
    [
        ["list", "--limit", "0"],
        ["list", "--limit", "501"],
        ["get", "id", "--offset", "-1"],
        ["list", "--before", "a", "--after", "b"],
        ["search"],
        ["replies"],
    ],
)
def test_invalid_read_arguments_fail_before_any_request(monkeypatch, args):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, args, {})
    assert exc.value.code == 2
