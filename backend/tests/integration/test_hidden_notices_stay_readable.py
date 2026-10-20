"""A notice kept out of the room is still part of the conversation's record.

Session output (a tool step, a subagent's conclusion, narration) is read from
session records, and the rows the old 现场 writers left in `blocks` are not
served as conversation any more. A hidden notice is not session output: the
platform writes it today (an edited message, a memory change, an unresolved
reference), and the reads that cover what the room keeps out
(`/blocks?shown=false`, the unfiltered timeline, `/history`) must keep
returning it.
"""

import asyncio
import uuid

from app.domain.block import editing
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from tests.integration.conftest import post_project


def _room_with_a_notice_and_a_leftover_step(client) -> tuple[str, str, str]:
    """Returns the room, the edited-message notice and a leftover tool step."""
    response = post_project(client, json={"name": "Notices"}, owner="alice")
    assert response.status_code == 200, response.text
    project = response.json()["data"]
    ids: dict[str, str] = {}

    async def seed() -> None:
        async with client.test_factory() as session:
            blocks = BlockRepository(session)
            said = await blocks.add(
                project_id=uuid.UUID(project["id"]),
                conversation_id=uuid.UUID(project["root_topic_id"]),
                author="alice",
                author_type=AuthorType.participant,
                content="first words",
            )
            notice = await editing._tell_the_room(blocks, said, "alice")
            step = await blocks.add(
                project_id=uuid.UUID(project["id"]),
                conversation_id=uuid.UUID(project["root_topic_id"]),
                author="cheese",
                author_type=AuthorType.participant,
                content="pytest -q",
                kind=BlockKind.event,
                meta={"in_room": False, "tool": "Bash", "arg": "pytest -q"},
            )
            await session.commit()
            ids["notice"], ids["step"] = str(notice.id), str(step.id)

    asyncio.run(seed())
    return project["root_topic_id"], ids["notice"], ids["step"]


def _ids(client, path: str, **params) -> list[str]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return [block["id"] for block in response.json()["data"]["data"]]


def test_reads_of_what_the_room_keeps_out_return_its_notices(client):
    room, notice, step = _room_with_a_notice_and_a_leftover_step(client)

    assert _ids(client, f"/topics/{room}/blocks", shown="false") == [notice]
    unfiltered = _ids(client, f"/topics/{room}/blocks")
    assert notice in unfiltered and step not in unfiltered
    history = _ids(client, f"/topics/{room}/history")
    assert notice in history and step not in history
    assert client.get(f"/topics/{room}/history/{notice}").status_code == 200
    assert client.get(f"/topics/{room}/history/{step}").status_code == 404
