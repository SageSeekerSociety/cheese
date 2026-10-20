"""Release two deletes the 现场 rows the removed writers produced, and only those.

`scripts/dev/count-retired-site.py` holds the deletion's `WHERE`. Hidden event
blocks are still written for edited-message notices, and agents read those
notices back as input. A predicate that matched them would delete live input.
The script also counts `run_records` for real, by kind, rather than asserting
a number.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.domain.agent.mentions import announce_mentions
from app.domain.block import editing
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.run_record.service import record
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered

SCRIPT = Path(__file__).parents[3] / "scripts" / "dev" / "count-retired-site.py"

# What the removed writers stored, one each: a tool step with output written
# onto it, a subagent's conclusion, a line of narration.
RETIRED = [
    {
        "in_room": False,
        "tool": "Bash",
        "platform": False,
        "arg": "pytest -q",
        "output": "1 passed",
        "eid": "e-tool",
    },
    {
        "in_room": False,
        "platform": False,
        "subagent": {
            "tool": "Task",
            "description": "look around",
            "summary": "found it",
            "truncated": False,
        },
    },
    {"in_room": False, "progress": True, "eid": "e-say"},
]


async def _count(factory) -> dict:
    url = factory.kw["bind"].url.render_as_string(hide_password=False)
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={"DATABASE_URL": url, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    counts: dict = {}
    for line in done.stdout.splitlines():
        counts.update(json.loads(line))
    return counts


@pytest.mark.anyio
async def test_only_the_removed_kinds_are_retired(business_db_factory):
    factory = business_db_factory
    async with factory() as session:
        await registered(session, "alice")
        project = await project_service(session).create(
            name="Retired", owner_handle="alice"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="Retired", created_by="alice"
        )
        blocks = BlockRepository(session)
        for meta in RETIRED:
            await blocks.add(
                project_id=project.id,
                conversation_id=room.id,
                author="cheese",
                author_type=AuthorType.participant,
                content="retired",
                kind=BlockKind.event,
                meta=meta,
            )
        # An edited message still creates a live hidden notice. Unresolved
        # references and retries belong to the platform's run-record ledger.
        said = await blocks.add(
            project_id=project.id,
            conversation_id=room.id,
            author="alice",
            author_type=AuthorType.participant,
            content="first words",
        )
        await editing._tell_the_room(blocks, said, "alice")
        agent_said = await blocks.add(
            project_id=project.id,
            conversation_id=room.id,
            author="cheese",
            author_type=AuthorType.participant,
            content="ask <@nobody-here>",
            turn_id=uuid.uuid4(),
        )
        await announce_mentions(
            session,
            room,
            agent_said,
            "cheese",
            [{"handle": "alice"}],
            flag_unresolved=True,
        )
        await record(
            session,
            conversation_id=room.id,
            content="retrying",
            meta={"event_type": "api_retry"},
        )
        await session.commit()

    counts = await _count(factory)

    assert counts["retired_site_rows"] == len(RETIRED), counts
    assert counts["tool_rows"] == 1
    assert counts["subagent_rows"] == 1
    assert counts["narration_rows"] == 1
    assert counts["rows_with_output"] == 1
    assert counts["hidden_rows_kept"] == 1, counts
    assert counts["run_records_by_kind"] == {"api_retry": 1, "unresolved_reference": 1}
