"""One poll advances an accept card at a time, without holding its row.

The poller asks the forge with no database connection checked out and no lock
on the card; a claim keeps a second poll (webhook or clock) off the card, a
crashed poll's claim lapses, and a failed poll gives its claim back."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from tests.integration.conftest import a_team


async def _card(db_factory, **fields):
    from app.domain.project.models import Project
    from app.domain.review.models import AcceptCard
    from app.domain.topic.models import Topic

    async with db_factory() as session:
        project = Project(team_id=await a_team(session), name="Poll claim test")
        session.add(project)
        await session.flush()
        room = Topic(project_id=project.id, title="Review room", created_by="requester")
        session.add(room)
        await session.flush()
        card = AcceptCard(
            topic_id=room.id, reviewer_handle="reviewer", pr_number=1, **fields
        )
        session.add(card)
        await session.commit()
        return card.id


async def _advance(db_factory, card_id):
    from app.domain.review.services import AcceptService

    async with db_factory() as session:
        await AcceptService(session).advance_pr_card(
            card_id, chat_service=None, runner=None
        )
        await session.commit()


async def test_the_card_is_not_locked_while_the_forge_answers(db_factory, monkeypatch):
    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService

    card_id = await _card(db_factory)
    entered, release = asyncio.Event(), asyncio.Event()

    async def poll(*args, **kwargs):
        entered.set()
        await release.wait()

    forge = SimpleNamespace(poll=AsyncMock(side_effect=poll))
    monkeypatch.setattr(AcceptService, "_resolve_forge", AsyncMock(return_value=forge))
    polling = asyncio.create_task(_advance(db_factory, card_id))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        async with db_factory() as other:
            await other.execute(text("SET LOCAL lock_timeout = '200ms'"))
            locked = await other.scalar(
                select(AcceptCard.id)
                .where(AcceptCard.id == card_id)
                .with_for_update(nowait=True)
            )
            assert locked == card_id
            await other.rollback()
    finally:
        release.set()
        await polling


async def test_a_lapsed_claim_is_taken_over(db_factory, monkeypatch):
    import uuid

    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService

    card_id = await _card(
        db_factory,
        poll_claim=uuid.uuid4(),
        poll_claimed_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    forge = SimpleNamespace(poll=AsyncMock())
    monkeypatch.setattr(AcceptService, "_resolve_forge", AsyncMock(return_value=forge))
    await _advance(db_factory, card_id)
    assert forge.poll.await_count == 1
    async with db_factory() as session:
        card = await session.get(AcceptCard, card_id)
        assert card.poll_claim is None and card.poll_claimed_until is None


async def test_a_live_claim_keeps_another_poll_off(db_factory, monkeypatch):
    import uuid

    from app.domain.review.services import AcceptService

    card_id = await _card(
        db_factory,
        poll_claim=uuid.uuid4(),
        poll_claimed_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    forge = SimpleNamespace(poll=AsyncMock())
    monkeypatch.setattr(AcceptService, "_resolve_forge", AsyncMock(return_value=forge))
    await _advance(db_factory, card_id)
    assert forge.poll.await_count == 0


async def test_a_failed_poll_gives_its_claim_back(db_factory, monkeypatch):
    from app.domain.review.services import AcceptService

    card_id = await _card(db_factory)
    forge = SimpleNamespace(poll=AsyncMock(side_effect=RuntimeError("forge down")))
    monkeypatch.setattr(AcceptService, "_resolve_forge", AsyncMock(return_value=forge))
    with pytest.raises(RuntimeError):
        await _advance(db_factory, card_id)
    forge.poll.side_effect = None
    await _advance(db_factory, card_id)
    assert forge.poll.await_count == 2


async def test_a_forge_call_in_a_poll_holds_no_transaction(db_factory):
    from app.domain.review.services.poll_claim import CommitsBeforeRemote

    seen = []

    class Forge:
        async def pull_request_status(self, session):
            seen.append(session.in_transaction())
            return "ok"

    async with db_factory() as session:
        await session.execute(text("SELECT 1"))
        assert session.in_transaction()
        client = CommitsBeforeRemote(Forge(), session)
        assert await client.pull_request_status(session) == "ok"
    assert seen == [False]
