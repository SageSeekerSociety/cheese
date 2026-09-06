"""A re-send must inherit the same continuation id.

The idempotency keys in ``domain.idempotency`` are all scoped to a continuation
id. If a re-sent turn ran under a FRESH one, every key the interrupted attempt
claimed would stop matching and all five side effects would be repeated — the
keys would still be there, still durable, and completely inert.

This is the load-bearing assertion: a request and the platform's one re-send of
it (the deploy-stranded case) are one unit of work — the re-send runs under the
dead turn's recorded continuation.
"""

import asyncio
import uuid

import pytest

from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from tests.turn_log import a_topic, open_turn


class _Quiet:
    session_factory = None

    async def converse(self, **_):
        yield {"type": "done"}

    async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
        return {"id": "b1", "content": text}

    def has_live_screen(self, topic_id):
        return False

    async def turns_that_produced_something(self, turn_ids):
        # No trace anywhere → the sweep may re-send (the path under test).
        return set()

    def schedule_spool_settle(self, topic_id, delay_s=2.0):
        return None


async def _until_finished(queue) -> None:
    """Read frames until the turn is really over.

    Ending a turn now closes its durable interval, so "the turn is finished" is
    no longer true the instant a frame arrives — the `turn_finished` frame is
    the runner saying it, and reading fewer frames than that is reading mid-turn.
    """
    async with asyncio.timeout(5):
        while (await queue.get())["type"] != "turn_finished":
            pass


def _wired(chat, factory):
    """Hand a stand-in ChatService the database the runner logs turns in."""
    chat.session_factory = factory
    return chat


def _capture_resends(runner, monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def _fake(_chat, tid, after, content, *, continuation_id=None):
        seen.append({"topic": tid, "continuation_id": continuation_id})

    monkeypatch.setattr(runner, "_schedule_resend", _fake)
    return seen


@pytest.mark.anyio
async def test_a_fresh_turn_starts_its_own_continuation(db_factory):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        turn_id = runner.submit(
            _wired(_Quiet(), db_factory), topic, author="u", content="hi", summon=True
        )
        await asyncio.wait_for(q.get(), 2)
    rec = runner.topic_work(topic)
    assert rec is not None
    # "第一次尝试的 continuation 就是它自己的 turn id" — the invariant the
    # orphan-sweep fallback also relies on.
    assert rec["continuation_id"] == str(turn_id)


@pytest.mark.anyio
async def test_orphan_resend_runs_under_the_recorded_continuation(
    db_factory, monkeypatch
):
    """The path that actually broke in production: the process dies, so the
    continuation has to come back out of the DATABASE, not out of memory. The
    sweep's remedy for an undelivered prompt is a re-send now, but the invariant
    is the same one: it runs under the dead turn's recorded continuation."""
    topic = await a_topic(db_factory)
    continuation = uuid.uuid4()
    await open_turn(db_factory, topic, continuation_id=continuation, age_s=60)
    runner = AgentWorkRunner(InProcessBroker())
    seen = _capture_resends(runner, monkeypatch)
    assert await runner.resume_orphans(_wired(_Quiet(), db_factory)) == 1
    assert seen[0]["continuation_id"] == continuation


@pytest.mark.anyio
async def test_continuation_for_is_none_outside_a_running_turn(db_factory):
    """What the endpoints key off: no running turn → no continuation → no
    dedup. A human clicking twice means it twice."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = await a_topic(db_factory)
    assert runner.continuation_for(topic) is None
    async with broker.subscribe(str(topic)) as q:
        runner.submit(
            _wired(_Quiet(), db_factory), topic, author="u", content="hi", summon=True
        )
        await _until_finished(q)
    # The turn finished; its record is still in the ring buffer but no longer
    # running, so a later stray call must not reuse its namespace.
    assert runner.continuation_for(topic) is None


# --- Who is driving: the other question the live turn record answers ---------
#
# 归属跟推进者走 (拍板 2026-08-17): `cheese split` runs under the 分身's own
# `cheese-<hex12>` handle, so the endpoint cannot see the person who asked for the
# split. That person is in the same `_recent` record as the continuation id, which
# is why both are read off it — and why they must agree about which turn "now" is.


class _Blocks:
    """A turn that reaches the middle and waits, so a caller can observe the
    runner WHILE a turn is live — which is the only state `turn_author_for` is
    allowed to answer from."""

    session_factory = None

    def __init__(self):
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    async def converse(self, **_):
        yield {"type": "user_block"}
        self.started.set()
        await self.finish.wait()
        yield {"type": "done"}


async def _while_running(runner, broker, topic, author: str, factory):
    """Start a turn for `author`, read both answers mid-flight, then let it end."""
    turn = _wired(_Blocks(), factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(turn, topic, author=author, content="hi", summon=True)
        await asyncio.wait_for(turn.started.wait(), 2)
        answer = runner.turn_author_for(topic)
        continuation = runner.continuation_for(topic)
        turn.finish.set()
        await asyncio.wait_for(q.get(), 2)
    return answer, continuation


@pytest.mark.anyio
async def test_the_human_driving_the_turn_is_reported(db_factory):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = await a_topic(db_factory)
    author, continuation = await _while_running(
        runner, broker, topic, "bob", db_factory
    )
    assert author == "bob"
    # Same record, so the split endpoint's two reads describe the same turn.
    assert continuation is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "author",
    [
        pytest.param("system", id="a_platform_initiated_turn"),
        pytest.param("cheese", id="the_platform_agent"),
        pytest.param("cheese-a7a0268b96ff", id="an_autonomous_分身"),
        pytest.param("anonymous", id="an_unidentified_caller"),
        pytest.param("", id="no_author_at_all"),
    ],
)
async def test_only_a_real_person_is_reported_as_the_driver(db_factory, author):
    """Gate verdicts, scheduled wake-ups, `cheese await` reports and conflict
    nudges all run as `system`; a 分身 working on its own initiative runs as
    itself. None of them may become a room's owner — `seed()` refuses to make 芝士
    an owner, so a room seeded from one lands ownerless and nobody can manage its
    roster. The caller falls back to the ladder it had instead."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = await a_topic(db_factory)
    reported, _ = await _while_running(runner, broker, topic, author, db_factory)
    assert reported is None


@pytest.mark.anyio
async def test_no_driver_outside_a_running_turn(db_factory):
    """Same rule as `continuation_for`: `_recent` remembers what turns DID, so a
    finished turn's author must not be read as whoever is driving now."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = await a_topic(db_factory)
    assert runner.turn_author_for(topic) is None
    async with broker.subscribe(str(topic)) as q:
        runner.submit(
            _wired(_Quiet(), db_factory), topic, author="bob", content="hi", summon=True
        )
        await _until_finished(q)
    assert runner.turn_author_for(topic) is None
