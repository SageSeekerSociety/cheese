"""A turn nobody can price still has to leave a trace.

The hooks backends run interactive Claude Code, which reports no token usage
locally; the gateway that would supply it is not configured everywhere. The old
code skipped the row entirely, so the usage table showed four rows across two
weeks while 300 RMB of relay credit drained with nothing naming what spent it.
Zero tokens is a worse answer than a real count, and a far better one than no row.
"""

import uuid

import pytest

from app.domain.usage.repositories import UsageRepository

pytestmark = pytest.mark.anyio


class _Session:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None


async def test_an_unmetered_turn_is_still_written():
    session = _Session()
    project, topic = uuid.uuid4(), uuid.uuid4()

    await UsageRepository(session).add(
        project_id=project,
        topic_id=topic,
        model="glm-4.6",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        metered=False,
    )

    (row,) = session.added
    assert row.project_id == project
    assert row.model == "glm-4.6", "the row must still name what ran"
    assert row.kind.endswith(":unmetered"), (
        "an unpriced turn must be distinguishable from a genuinely free one"
    )


async def test_a_metered_turn_keeps_its_plain_kind():
    """Marking everything unmetered would make the flag useless."""
    session = _Session()

    await UsageRepository(session).add(
        project_id=uuid.uuid4(),
        topic_id=None,
        model="glm-4.6",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
    )

    (row,) = session.added
    assert row.kind == "chat"
    assert row.total_tokens == 120
