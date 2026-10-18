"""A person's message is written in its own session, and that session takes what
the request already read of the project — its row and its roster — instead of
reading them again (`HumanHandoff`). Without the handoff it reads them itself,
and either way the message is prepared the same."""

import re

import pytest
from sqlalchemy import event

from app.domain.agent.turn.intake.human import prepare_human
from app.domain.agent.turn.store.human import HumanHandoff, HumanPreparation
from app.domain.agent_instance.adapters import project_team
from app.domain.membership.roster import roster
from app.domain.project.reads import load_project
from app.domain.room_task.place import PlaceResolver
from tests.turn_log import a_topic

pytestmark = pytest.mark.anyio

#: The tables behind the project row and its roster.
_HANDED_OVER = re.compile(r"\bFROM (projects|project_members|user_profile)\b", re.I)

#: A friendly name the roster turns into a mention token.
_CONTENT = "@u 看一下这个"


async def _prepare(
    db_factory, topic_id, handoff: HumanHandoff | None
) -> tuple[dict, list[str]]:
    """Prepare the message in a writer session of its own: what it prepared,
    and the statements that session sent to the handed-over tables."""
    seen: list[str] = []

    def count(conn, cursor, statement, *rest):
        if _HANDED_OVER.search(statement):
            seen.append(statement)

    async with db_factory() as session:
        place = await PlaceResolver(session).conversation(topic_id)
        assert place is not None
        engine = session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", count)
        try:
            prepared = await prepare_human(
                session, place, _CONTENT, "u", handoff=handoff
            )
        finally:
            event.remove(engine, "before_cursor_execute", count)
        fields = _fields(prepared)
        await session.rollback()
    return fields, seen


def _fields(prepared: HumanPreparation) -> dict:
    return {
        "project": prepared.project.id,
        "content": prepared.content,
        "roster": prepared.roster,
        "agent_handles": prepared.agent_handles,
        "named_seats": prepared.named_seats,
        "recipient": prepared.recipient,
    }


async def test_the_writer_takes_the_requests_project_and_roster(db_factory):
    topic_id = await a_topic(db_factory)
    async with db_factory() as request:
        place = await PlaceResolver(request).conversation(topic_id)
        assert place is not None
        handoff = HumanHandoff(
            project=await load_project(request, place.project_id),
            roster=await roster(request, place.project_id, team=project_team),
        )

    handed, handed_reads = await _prepare(db_factory, topic_id, handoff)
    read, own_reads = await _prepare(db_factory, topic_id, None)

    # Reading them itself is what the handoff saves: the comparison bites.
    assert own_reads
    assert handed_reads == []
    assert "<@u>" in read["content"]
    assert handed == read


async def test_a_partial_handoff_reads_only_what_it_lacks(db_factory):
    topic_id = await a_topic(db_factory)
    async with db_factory() as request:
        place = await PlaceResolver(request).conversation(topic_id)
        assert place is not None
        project = await load_project(request, place.project_id)

    handed, reads = await _prepare(db_factory, topic_id, HumanHandoff(project=project))
    read, _ = await _prepare(db_factory, topic_id, None)

    # The roster did not come with the request, so it is still read here.
    assert any(re.search(r"\bFROM project_members\b", s, re.I) for s in reads)
    assert handed == read
