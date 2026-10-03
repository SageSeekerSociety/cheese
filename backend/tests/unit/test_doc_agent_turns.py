"""Whose turn it is to be answered, among a project's document threads.

- One question of a thread is answered at a time; the next one waits for it.
- At most ``ANSWERING_PER_PROJECT`` of a project's threads are answered at
  once; one more waits for a turn, and gets it as soon as one finishes.
- Another project's threads do not wait on this one's.
- A question that waits longer than the wait allows is given up on, and holds
  nothing afterwards.

Against a real Redis: the holds are what other backend processes see.
"""

import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.api import doc_agent
from app.core.config import settings
from app.domain.agent import admission


@pytest.fixture
async def redis(monkeypatch):
    monkeypatch.setattr(admission, "POLL_S", 0.05)
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


@pytest.mark.anyio
async def test_a_fifth_thread_waits_until_one_of_four_finishes(redis):
    project = uuid.uuid4()
    threads = [uuid.uuid4() for _ in range(doc_agent.ANSWERING_PER_PROJECT)]
    slots = [await doc_agent.take_turn(redis, project, thread) for thread in threads]
    fifth = uuid.uuid4()

    waiting = asyncio.create_task(doc_agent.take_turn(redis, project, fifth))
    await asyncio.sleep(0.3)
    assert not waiting.done()

    assert slots[0] is not None
    await slots[0].release()
    assert await asyncio.wait_for(waiting, 2) is not None


@pytest.mark.anyio
async def test_another_projects_thread_does_not_wait(redis):
    project = uuid.uuid4()
    for _ in range(doc_agent.ANSWERING_PER_PROJECT):
        await doc_agent.take_turn(redis, project, uuid.uuid4())

    elsewhere = await asyncio.wait_for(
        doc_agent.take_turn(redis, uuid.uuid4(), uuid.uuid4()), 1
    )

    assert elsewhere is not None


@pytest.mark.anyio
async def test_a_threads_next_question_waits_for_the_one_being_answered(redis):
    project, thread = uuid.uuid4(), uuid.uuid4()
    slot = await doc_agent.take_turn(redis, project, thread)
    assert slot is not None

    second = asyncio.create_task(doc_agent.take_turn(redis, project, thread))
    await asyncio.sleep(0.3)
    assert not second.done()

    await slot.release()
    assert await asyncio.wait_for(second, 2) is not None


@pytest.mark.anyio
async def test_a_question_that_waits_too_long_is_given_up_and_holds_nothing(
    redis, monkeypatch
):
    monkeypatch.setattr(doc_agent, "WAIT_S", 0.3)
    project = uuid.uuid4()
    for _ in range(doc_agent.ANSWERING_PER_PROJECT):
        await doc_agent.take_turn(redis, project, uuid.uuid4())
    late = uuid.uuid4()

    assert await doc_agent.take_turn(redis, project, late) is None
    assert not await doc_agent.asked(redis, late)
