"""A poller that reports every network blip is a poller nobody reads.

`poll_open_prs` runs every 60 seconds over every open PR card, and each tick
talks to GitHub and to the device connection owner. Both go away briefly and
come back: a release restarts the owner, DNS misses, a handshake times out. The
tick after fixes it — but each miss was `logger.exception`, so each one became
an alert, and 39 of the alert channel's first 600 messages were this. None of
them were acted on and none of them could be: by the time anyone looked the
retry had already succeeded.

What is worth waking someone for is the retries NOT working. That is what these
tests pin: a blip is a warning, a blip that will not stop is an error, and a
failure that is not the network at all is an error immediately — a bug in the
poller is not something a later tick repairs.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.domain.agent.announce import announce
from app.domain.review import pr_poll
from app.domain.review.pr_poll import TRANSIENT_MISSES_BEFORE_ERROR
from tests.support.review_ports import (
    record_task_instruction,
    settle_card,
    task_teammate,
)

pytestmark = pytest.mark.anyio

# 生产里这三个口子由 `app/api/routes/accept.py` 的接线递进来（`review` 够不着
# `delivery`）。这里 AcceptService 是假的、口子用不到，递真实现即可 —— 跑的还是
# poll 自己那条路。
_PORTS = {
    "settle_card": settle_card,
    "record_instruction": record_task_instruction,
    "task_teammate": task_teammate,
}


@pytest.fixture(autouse=True)
def _forget_misses_between_tests():
    """一张卡连丢了几拍是进程状态：一条用例留下的计数会让下一条提前（或推迟）报错。"""
    pr_poll._transient_misses.clear()
    yield
    pr_poll._transient_misses.clear()


class _Session:
    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


def _poller(monkeypatch, failure: Exception) -> tuple[SimpleNamespace, dict]:
    """A chat binding whose one card fails with `failure` until `next` is emptied."""
    card_id = uuid.uuid4()
    next_outcome: dict = {"failure": failure}

    @asynccontextmanager
    async def sessions():
        yield _Session()

    class _AcceptService:
        def __init__(self, _session, *, announce=None, **_ports) -> None: ...  # noqa: ARG002

        async def open_pr_card_ids(self, project_id=None) -> list[uuid.UUID]:
            assert project_id is None
            return [card_id]

        async def advance_pr_card(self, *_args, **_kwargs) -> None:
            if next_outcome["failure"] is not None:
                raise next_outcome["failure"]

    monkeypatch.setattr(
        "app.domain.review.services.AcceptService", _AcceptService, raising=True
    )
    monkeypatch.setattr(
        pr_poll, "_note_card_poll_crashed", _nothing_on_the_card, raising=True
    )
    # The runner now comes off the chat service; nothing below uses it.
    return SimpleNamespace(session_factory=sessions, work_runner=None), next_outcome


async def _nothing_on_the_card(*_args, **_kwargs) -> None:
    """The card note has its own transaction and its own tests."""


def _errors(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_one_missed_tick_is_not_worth_waking_anyone(monkeypatch, caplog):
    chat, _outcome = _poller(monkeypatch, httpx.ConnectError("no route to host"))

    with caplog.at_level(logging.DEBUG):
        result = await pr_poll.poll_open_prs(chat, announce, **_PORTS)

    assert _errors(caplog) == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    # The failure is still counted — silence in the channel is not silence in
    # the return value that the tick reports.
    assert result["errors"]


async def test_retries_that_keep_failing_are_reported(monkeypatch, caplog):
    chat, _outcome = _poller(monkeypatch, httpx.ConnectError("no route to host"))

    with caplog.at_level(logging.DEBUG):
        for _ in range(TRANSIENT_MISSES_BEFORE_ERROR):
            await pr_poll.poll_open_prs(chat, announce, **_PORTS)

    assert len(_errors(caplog)) == 1


async def test_a_failure_that_is_not_the_network_is_reported_at_once(
    monkeypatch, caplog
):
    chat, _outcome = _poller(monkeypatch, KeyError("pr_number"))

    with caplog.at_level(logging.DEBUG):
        await pr_poll.poll_open_prs(chat, announce, **_PORTS)

    assert len(_errors(caplog)) == 1


async def test_a_tick_that_works_forgives_the_misses_before_it(monkeypatch, caplog):
    """Two blips an hour apart are two blips, not a machine on its way out."""
    chat, outcome = _poller(monkeypatch, httpx.ConnectError("blip"))

    with caplog.at_level(logging.DEBUG):
        await pr_poll.poll_open_prs(chat, announce, **_PORTS)
        outcome["failure"] = None
        await pr_poll.poll_open_prs(chat, announce, **_PORTS)
        outcome["failure"] = httpx.ConnectError("blip")
        for _ in range(TRANSIENT_MISSES_BEFORE_ERROR - 1):
            await pr_poll.poll_open_prs(chat, announce, **_PORTS)

    assert _errors(caplog) == []
