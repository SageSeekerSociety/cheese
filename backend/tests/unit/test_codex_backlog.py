"""Reconnect replays identical records; retention preserves child attribution."""

from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import Backlog
from app.domain.agent.harness.codex.backlog import CodexBacklog, receive
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.service import AgentMessage, AgentSubagentStop


def entry(sequence, method, params):
    return {
        "sequence": sequence,
        "at": "2026-09-12T00:00:00+00:00",
        "record": {"method": method, "params": params},
    }


@pytest.mark.anyio
async def test_failed_receive_resumes_after_committed_page_without_consuming(tmp_path):
    path = tmp_path / "mirror.sqlite"
    page = [entry(i, "fixture", {}) for i in range(1, 257)]
    failed = AsyncMock(side_effect=[{"events": page}, ConnectionError("offline")])
    with pytest.raises(ConnectionError):
        await receive(path, failed)
    resumed = AsyncMock(return_value={"events": [entry(257, "fixture", {})]})
    await receive(path, resumed)
    resumed.assert_awaited_once_with("events", {"after": 256})
    backlog = CodexBacklog(path)
    assert isinstance(backlog, Backlog)
    assert len(backlog.unread()) == 257
    assert [(event.key, event.record) for event in backlog.unread()] == [
        (event.key, event.record) for event in CodexBacklog(path).unread()
    ]
    backlog.landed(through=backlog.unread()[255].key)
    backlog.landed(through=backlog.unread()[0].key)
    assert len(CodexBacklog(path).unread()) == 1


@pytest.mark.anyio
async def test_reconnect_completes_partial_child_after_start_record_is_pruned(tmp_path):
    path = tmp_path / "mirror.sqlite"
    start = entry(
        1,
        "thread/started",
        {
            "thread": {
                "id": "child",
                "parentThreadId": "root",
                "agentRole": "explorer",
            }
        },
    )
    partial = entry(
        2,
        "item/agentMessage/delta",
        {
            "threadId": "child",
            "itemId": "item",
            "delta": "part",
        },
    )
    await receive(path, AsyncMock(return_value={"events": [start, partial]}))
    first = CodexBacklog(path)
    first.assemble(first.unread()[0])
    first.landed(through=first.unread()[0].key)
    assert first.assemble(first.unread()[1]) == []
    assert first.unfinished() == {"codex:child:item"}
    first.forget(older_than_s=0)
    journal = Journal(path)
    assert [row["sequence"] for row in journal.read()] == [2]
    journal.close()
    complete = entry(
        3,
        "item/completed",
        {
            "threadId": "child",
            "item": {
                "type": "agentMessage",
                "id": "item",
                "text": "whole",
            },
        },
    )
    stop = entry(
        4,
        "turn/completed",
        {
            "threadId": "child",
            "turn": {
                "id": "turn",
                "status": "completed",
                "items": [],
            },
        },
    )
    await receive(path, AsyncMock(return_value={"events": [complete, stop]}))
    second = CodexBacklog(path)
    events = [event for row in second.unread() for event in second.assemble(row)]
    assert isinstance(events[0], AgentMessage)
    assert events[0].text == "whole"
    assert events[0].agent_id == "child"
    assert isinstance(events[1], AgentSubagentStop)
    assert events[1].session_id == "root"
    assert not second.unfinished()
    third = CodexBacklog(path)
    replayed = [event for row in third.unread() for event in third.assemble(row)]
    assert events[0].eid == replayed[0].eid
