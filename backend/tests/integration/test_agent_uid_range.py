"""Agent ids come from a range of their own.

Deliberately NOT a second identity column and not an invariant. What a row *is*
still comes from its ``AgentBinding``; the range exists so a uid is legible to
whoever is reading one. Both halves are pinned here — agents land inside it, and
something inside it without a binding is still just a person.

These tests read ids back instead of computing them, which is also what keeps
``AGENT_UID_START`` in ``app.domain.identity.uids`` and the number written into
the migration from drifting apart.
"""

from sqlalchemy import text

from app.domain.identity.services import IdentityService
from app.domain.identity.uids import AGENT_UID_SEQUENCE_NAME, AGENT_UID_START
from app.domain.user.repositories import UserRepository


async def test_an_agent_is_numbered_inside_the_agents_range(db_factory):
    """The point of the change: a new agent's uid is recognisable as one."""
    async with db_factory() as session:
        agent = await IdentityService(session).ensure_agent_user(
            handle="cheese-range-a"
        )

    assert agent.id >= AGENT_UID_START


async def test_a_person_is_numbered_by_the_human_sequence(db_factory):
    """Registration is untouched: people keep the small ids they always had.

    This is the other half of "a range", and the one that would break quietly —
    if the agent sequence and the identity column were ever the same thing, a
    person's uid would start looking like an agent's.
    """
    async with db_factory() as session:
        person = await UserRepository(session).create_user(
            username="range-person", email="range-person@example.com"
        )

    assert person.id < AGENT_UID_START


async def test_two_agents_do_not_share_a_number(db_factory):
    """Handed out by the database, so the ids are distinct and in order."""
    async with db_factory() as session:
        service = IdentityService(session)
        first = await service.ensure_agent_user(handle="cheese-range-b")
        second = await service.ensure_agent_user(handle="cheese-range-c")

    assert first.id < second.id
    assert second.id >= AGENT_UID_START


async def test_the_range_does_not_decide_who_is_an_agent(db_factory):
    """A row inside the range with no binding is a person, and must read as one.

    Nothing in the platform is allowed to answer "is this an agent?" by looking
    at the number — this is what would start failing if someone ever did.
    """
    async with db_factory() as session:
        users = UserRepository(session)
        impostor = await users.create_user(
            username="range-impostor",
            email="range-impostor@example.com",
            user_id=AGENT_UID_START,
        )

        assert await IdentityService(session).is_agent("range-impostor") is False
        assert impostor.id == AGENT_UID_START


async def test_the_sequence_starts_where_the_code_says(db_factory):
    """The migration's number and the constant agree, read from the database.

    Without this, ``AGENT_UID_START`` drifting from the migration would only
    show up as a test asserting the wrong thing in a way that still passes.
    """
    async with db_factory() as session:
        start = await session.scalar(
            text(
                "SELECT seqstart FROM pg_sequence "
                f"WHERE seqrelid = '{AGENT_UID_SEQUENCE_NAME}'::regclass"
            )
        )

    assert start == AGENT_UID_START
