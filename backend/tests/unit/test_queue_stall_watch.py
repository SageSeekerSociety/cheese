"""入队后迟迟没有 merge_group run：卡上标一次，run 起来或条目离队就清掉。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.domain.review import notes
from app.domain.review.github_pr import GitHubPrError, HttpxGitHubPrClient
from app.domain.review.models import AcceptStatus
from app.domain.review.services.polling import card_poll
from app.domain.review.services.polling.card_poll import (
    QUEUE_STALL_MARK,
    QUEUE_STALL_MINUTES,
    _clear_stall_mark,
    _poll_pr_card,
    _watch_queue_stall,
)

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


class _Svc:
    def __init__(self) -> None:
        self.told: list[str] = []

    async def _tell_the_reviewer(self, card, topic, content, *, meta, also=()):
        self.told.append(content)


class _Client:
    def __init__(self, started: bool) -> None:
        self.started = started
        self.calls = 0

    async def merge_group_run_started(self, **kwargs):
        self.calls += 1
        self.since = kwargs["since"]
        return self.started


def _card():
    card = SimpleNamespace(
        id="c1",
        pr_number=4081,
        pr_url="https://x/pull/4081",
        decided_at=T0,
        note="",
        note_code=None,
    )
    notes.record(card, notes.NoteCode.waiting_merge_queue, "PR #4081 已进入合并队列")
    return card


async def _watch(svc, card, client, minutes):
    await _watch_queue_stall(
        svc,
        card=card,
        topic=None,
        owner="o",
        repo="r",
        creds=SimpleNamespace(read="t"),
        client=client,
        now=T0 + timedelta(minutes=minutes),
    )


@pytest.mark.anyio
async def test_marks_once_after_threshold_without_run():
    svc, card, client = _Svc(), _card(), _Client(started=False)
    assert QUEUE_STALL_MINUTES == 30
    await _watch(svc, card, client, 29)
    assert QUEUE_STALL_MARK not in card.note and client.calls == 0
    await _watch(svc, card, client, 31)
    await _watch(svc, card, client, 32)
    assert card.note.count(QUEUE_STALL_MARK) == 1
    assert len(svc.told) == 1
    assert client.since == T0  # only runs created after the entry count
    assert card.note_code is notes.NoteCode.waiting_merge_queue


@pytest.mark.anyio
async def test_started_run_is_not_marked():
    svc, card = _Svc(), _card()
    await _watch(svc, card, _Client(started=True), 90)
    assert QUEUE_STALL_MARK not in card.note and not svc.told


@pytest.mark.anyio
async def test_run_start_clears_mark():
    svc, card = _Svc(), _card()
    await _watch(svc, card, _Client(started=False), 40)
    assert QUEUE_STALL_MARK in card.note
    await _watch(svc, card, _Client(started=True), 50)
    assert QUEUE_STALL_MARK not in card.note
    assert card.note == "PR #4081 已进入合并队列"


def test_leaving_queue_clears_mark():
    card = _card()
    notes.annotate(card, f"{QUEUE_STALL_MARK} 40 分钟，还没轮到")
    _clear_stall_mark(card)
    assert QUEUE_STALL_MARK not in card.note


@pytest.mark.anyio
async def test_client_without_lookup_is_skipped():
    svc, card = _Svc(), _card()
    await _watch(svc, card, SimpleNamespace(), 90)
    assert not svc.told and QUEUE_STALL_MARK not in card.note


@pytest.mark.anyio
async def test_lookup_error_does_not_raise():
    class Boom:
        async def merge_group_run_started(self, **_):
            raise RuntimeError("github down")

    svc, card = _Svc(), _card()
    await _watch(svc, card, Boom(), 90)
    assert not svc.told


@pytest.mark.anyio
async def test_card_poll_in_queue_runs_the_stall_watch(monkeypatch):
    """The wiring: a poll of a queued card past the threshold marks it."""

    class Client(_Client):
        async def pull_request_status(self, **_):
            return SimpleNamespace(merged=False, state="open", head_sha="h")

        async def merge_queue_entry(self, **_):
            return True

    class Svc(_Svc):
        class _session:  # minimal stand-in for the db session
            @staticmethod
            async def flush():
                return None

            @staticmethod
            async def refresh(instance, attribute_names=None, with_for_update=None):
                # The mirror write re-reads the card under its row lock; here
                # the card object is the only copy, so there is nothing newer.
                return None

    svc, card, client = Svc(), _card(), Client(started=False)
    card.status = AcceptStatus.accepted
    card.pr_head_sha = "h"
    card.auto_merge_armed_by = None
    card.merge_state = None
    real = card_poll.datetime
    monkeypatch.setattr(
        card_poll,
        "datetime",
        SimpleNamespace(now=lambda tz=None: T0 + timedelta(minutes=45)),
    )
    try:
        await _poll_pr_card(
            svc,
            card=card,
            topic=None,
            owner="o",
            repo="r",
            creds=SimpleNamespace(read="t"),
            client=client,
            chat_service=None,
            runner=None,
        )
    finally:
        monkeypatch.setattr(card_poll, "datetime", real)
    assert QUEUE_STALL_MARK in card.note and len(svc.told) == 1


@pytest.mark.anyio
async def test_client_sends_created_filter_and_raises_on_error():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(403, text="no")

    client = HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubPrError):
        await client.merge_group_run_started(
            owner="o", repo="r", number=1, token="t", since=T0
        )
    assert seen["created"] == ">=2026-10-17T12:00:00Z"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("gh-readonly-queue/main/pr-4081-abc", True),
        ("gh-readonly-queue/main/pr-40811-abc", False),
        ("gh-readonly-queue/main/pr-1-abc", False),
    ],
)
async def test_client_matches_pr_number_in_head_branch(branch, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["event"] == "merge_group"
        return httpx.Response(200, json={"workflow_runs": [{"head_branch": branch}]})

    client = HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    got = await client.merge_group_run_started(
        owner="o", repo="r", number=4081, token="t", since=T0
    )
    assert got is expected
