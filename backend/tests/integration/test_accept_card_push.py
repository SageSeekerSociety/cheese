"""A card's pages are told when what they show of it changed, and only then.

Every GitHub event on a card's pull request, and every read of a card whose
merge state is a minute old, checks that state again and writes it back with
the time of the check. A page told each time reads the cards again, and that
read checks the next card due: a channel with five pending cards was told every
two seconds, and every open page downloaded its cards each time.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.review import live
from app.domain.review.models import AcceptCard
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept import _make_card, _make_project, _make_topic
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_pr import app_world as app_world


@pytest.fixture(autouse=True)
def _owner(client):
    client.headers.update(session_auth_headers("alice"))
    yield
    client.headers.pop("Authorization", None)


class _Told:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def publish(self, channel: str, frame: dict) -> None:
        self.frames.append((channel, frame))


@pytest.fixture
def told(monkeypatch) -> _Told:
    broker = _Told()
    monkeypatch.setattr(live, "get_broker", lambda: broker)
    return broker


def _rewrite(card_id: str, change) -> None:
    async def run() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine)() as session:
                card = await session.get(AcceptCard, uuid.UUID(card_id))
                assert card is not None
                card.merge_state = change(dict(card.merge_state or {}))
                await session.commit()
            await asyncio.sleep(0.05)  # the frames go out after the commit
        finally:
            await engine.dispose()

    asyncio.run(run())


def _checked_now(state: dict) -> dict:
    return {**state, "checked_at": datetime.now(UTC).isoformat()}


def test_a_check_that_finds_the_same_state_tells_nobody(client, told):
    card = _make_card(client, _make_topic(client, _make_project(client)))
    _rewrite(card, lambda s: {**s, "state": "clean"})
    told.frames.clear()

    _rewrite(card, _checked_now)

    assert told.frames == []


def test_a_check_that_finds_a_new_state_tells_the_card_s_pages(client, told):
    card = _make_card(client, _make_topic(client, _make_project(client)))
    _rewrite(card, lambda s: {**s, "state": "clean"})
    told.frames.clear()

    _rewrite(card, lambda s: _checked_now({**s, "state": "blocked"}))

    assert {frame["resource"] for _, frame in told.frames} == {"accept", "tasks"}
