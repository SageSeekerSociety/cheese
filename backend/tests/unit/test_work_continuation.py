"""Automatic retries must inherit the same continuation id.

The idempotency keys in ``domain.idempotency`` are all scoped to a continuation
id. If an auto-resume ran under a FRESH one, every key the interrupted attempt
claimed would stop matching and all five side effects would be repeated — the
keys would still be there, still durable, and completely inert.

This is the load-bearing assertion: one request and every automatic continuation
are one unit of work on all three paths
(timeout resume / crash resume / restart re-send).
"""

import asyncio
import time as _time
import uuid

import pytest

from app.domain.agent import runtime as rt
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker


class _Hang:
    """A turn that never finishes — what the wall-clock ceiling exists for."""

    async def converse(self, **_):
        yield {"type": "user_block"}
        await asyncio.sleep(10)
        yield {"type": "done"}  # pragma: no cover


class _Boom:
    async def converse(self, **_):
        yield {"type": "user_block"}
        raise RuntimeError("turn crashed")


class _Quiet:
    async def converse(self, **_):
        yield {"type": "done"}

    async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
        return {"id": "b1", "content": text}

    async def orphan_turn_evidence(self, topic_id, turn_ids):
        # No trace anywhere → the sweep may re-send (the path under test).
        return {"delivered": set(), "spool": False}

    def schedule_spool_settle(self, topic_id, delay_s=2.0):
        return None


def _capture_resumes(runner, monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def _fake(_chat, tid, after, why="", *, continuation_id=None):
        seen.append({"topic": tid, "continuation_id": continuation_id})

    monkeypatch.setattr(runner, "_schedule_resume", _fake)
    return seen


def _capture_resends(runner, monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def _fake(_chat, tid, after, content, *, continuation_id=None):
        seen.append({"topic": tid, "continuation_id": continuation_id})

    monkeypatch.setattr(runner, "_schedule_resend", _fake)
    return seen


@pytest.mark.anyio
async def test_a_fresh_turn_starts_its_own_continuation():
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        turn_id = runner.submit(_Quiet(), topic, author="u", content="hi", summon=True)
        await asyncio.wait_for(q.get(), 2)
    rec = runner.topic_work(topic)
    assert rec is not None
    # "第一次尝试的 continuation 就是它自己的 turn id" — the invariant the
    # orphan-sweep fallback also relies on.
    assert rec["continuation_id"] == str(turn_id)


@pytest.mark.anyio
async def test_timeout_resume_inherits_the_continuation(monkeypatch):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=0.05)
    seen = _capture_resumes(runner, monkeypatch)
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        turn_id = runner.submit(_Hang(), topic, author="u", content="hi", summon=True)
        for _ in range(3):
            f = await asyncio.wait_for(q.get(), 2)
            if f["type"] == "error":
                break
    for _ in range(200):  # the resume is scheduled just after the error frame
        if seen:
            break
        await asyncio.sleep(0.01)
    assert [s["continuation_id"] for s in seen] == [turn_id]


@pytest.mark.anyio
async def test_crash_resume_inherits_the_continuation(monkeypatch):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    seen = _capture_resumes(runner, monkeypatch)
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        turn_id = runner.submit(_Boom(), topic, author="u", content="hi", summon=True)
        for _ in range(4):
            f = await asyncio.wait_for(q.get(), 2)
            if f["type"] == "error":
                break
    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert [s["continuation_id"] for s in seen] == [turn_id]


@pytest.mark.anyio
async def test_orphan_resend_runs_under_the_recorded_continuation(
    tmp_path, monkeypatch
):
    """The path that actually broke in production: the process dies, so the
    continuation has to come back off DISK, not out of memory. The sweep's
    remedy for an undelivered prompt is a re-send now, but the invariant is the
    same one: it runs under the dead turn's recorded continuation."""
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    continuation = uuid.uuid4()
    rt._save_inflight(
        {
            str(uuid.uuid4()): {
                "topic_id": str(topic),
                "started_at": _time.time() - 60,
                "is_resume": False,
                "continuation_id": str(continuation),
                "author": "u",
                "content": "修一下登录页",
            }
        }
    )
    runner = AgentWorkRunner(InProcessBroker())
    seen = _capture_resends(runner, monkeypatch)
    assert await runner.resume_orphans(_Quiet()) == 1
    assert seen[0]["continuation_id"] == continuation


@pytest.mark.anyio
async def test_legacy_orphan_entry_falls_back_to_its_turn_id(tmp_path, monkeypatch):
    """An entry written before the continuation field existed still re-sends —
    under its own turn id, which IS what its continuation would have been."""
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    turn_id = uuid.uuid4()
    rt._save_inflight(
        {
            str(turn_id): {
                "topic_id": str(topic),
                "started_at": _time.time() - 60,
                "is_resume": False,
                "author": "u",
                "content": "修一下登录页",
            }
        }
    )
    runner = AgentWorkRunner(InProcessBroker())
    seen = _capture_resends(runner, monkeypatch)
    assert await runner.resume_orphans(_Quiet()) == 1
    assert seen[0]["continuation_id"] == turn_id


@pytest.mark.anyio
async def test_an_unparseable_entry_still_resends_instead_of_killing_the_sweep(
    tmp_path, monkeypatch
):
    """A corrupt entry must not raise out of the sweep: the sweep is what
    rescues every OTHER orphaned turn, so one bad row taking it down would be a
    worse failure than the one it is there to prevent."""
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    good_topic = uuid.uuid4()
    rt._save_inflight(
        {
            "not-a-uuid": {
                "topic_id": str(uuid.uuid4()),
                "started_at": _time.time() - 60,
                "is_resume": False,
                "author": "u",
                "content": "任务甲",
            },
            str(uuid.uuid4()): {
                "topic_id": str(good_topic),
                "started_at": _time.time() - 60,
                "is_resume": False,
                "continuation_id": str(uuid.uuid4()),
                "author": "u",
                "content": "任务乙",
            },
        }
    )
    runner = AgentWorkRunner(InProcessBroker())
    seen = _capture_resends(runner, monkeypatch)
    assert await runner.resume_orphans(_Quiet()) == 2
    assert all(isinstance(s["continuation_id"], uuid.UUID) for s in seen)
    assert good_topic in {s["topic"] for s in seen}


@pytest.mark.anyio
async def test_continuation_for_is_none_outside_a_running_turn():
    """What the endpoints key off: no running turn → no continuation → no
    dedup. A human clicking twice means it twice."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = uuid.uuid4()
    assert runner.continuation_for(topic) is None
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Quiet(), topic, author="u", content="hi", summon=True)
        await asyncio.wait_for(q.get(), 2)
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

    def __init__(self):
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    async def converse(self, **_):
        yield {"type": "user_block"}
        self.started.set()
        await self.finish.wait()
        yield {"type": "done"}


async def _while_running(runner, broker, topic, author: str):
    """Start a turn for `author`, read both answers mid-flight, then let it end."""
    turn = _Blocks()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(turn, topic, author=author, content="hi", summon=True)
        await asyncio.wait_for(turn.started.wait(), 2)
        answer = runner.turn_author_for(topic)
        continuation = runner.continuation_for(topic)
        turn.finish.set()
        await asyncio.wait_for(q.get(), 2)
    return answer, continuation


@pytest.mark.anyio
async def test_the_human_driving_the_turn_is_reported():
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = uuid.uuid4()
    author, continuation = await _while_running(runner, broker, topic, "bob")
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
async def test_only_a_real_person_is_reported_as_the_driver(author):
    """Gate verdicts, scheduled wake-ups, `cheese await` reports and conflict
    nudges all run as `system`; a 分身 working on its own initiative runs as
    itself. None of them may become a room's owner — `seed()` refuses to make 芝士
    an owner, so a room seeded from one lands ownerless and nobody can manage its
    roster. The caller falls back to the ladder it had instead."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = uuid.uuid4()
    reported, _ = await _while_running(runner, broker, topic, author)
    assert reported is None


@pytest.mark.anyio
async def test_no_driver_outside_a_running_turn():
    """Same rule as `continuation_for`: `_recent` remembers what turns DID, so a
    finished turn's author must not be read as whoever is driving now."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = uuid.uuid4()
    assert runner.turn_author_for(topic) is None
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Quiet(), topic, author="bob", content="hi", summon=True)
        await asyncio.wait_for(q.get(), 2)
    assert runner.turn_author_for(topic) is None
