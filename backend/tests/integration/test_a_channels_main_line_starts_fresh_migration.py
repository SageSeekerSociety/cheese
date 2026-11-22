"""5a8c1e3f7b20: the first call to 芝士 in an old channel's main line reads
only what was said after the release.

Before the release, a call in a channel's main line was answered in a 支线
opened under it; the main line had no session of its own. A teammate's read
position there, and any session it had there, date from before that. The rules,
as a person would state them:

- The first turn in an old channel's main line is handed only what was said
  after the release: not the questions it already answered in their 支线.
- That first turn opens a new conversation: nothing it resumes from October.
- A private chat is not a channel; it keeps where it was and what it resumes.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.agent_session.services import AgentSessionService
from app.domain.block.queries import inputs_after
from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "c7e2a5d9f031"
AFTER = "5a8c1e3f7b20"
SEAT = "cheese-0a1b2c3d4e5f"


def _say(
    db: ReplayDatabase,
    project: uuid.UUID,
    conversation: uuid.UUID,
    content: str,
    *,
    at: datetime,
    author: str = "alice",
    meta: dict | None = None,
) -> uuid.UUID:
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
        " author, content, refs, meta, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'message', 'participant', $4, $5, '[]', $6::json,"
        " $7, $7)",
        block,
        project,
        conversation,
        author,
        content,
        json.dumps(meta) if meta else None,
        at,
    )
    return block


def _seat(db: ReplayDatabase, room: uuid.UUID) -> None:
    db.execute(
        "INSERT INTO topic_memberships (id, topic_id, member_handle, role,"
        " created_at, updated_at) VALUES ($1, $2, $3, 'member', now(), now())",
        uuid.uuid4(),
        room,
        SEAT,
    )


def _session(db: ReplayDatabase, conversation: uuid.UUID) -> None:
    db.execute(
        "INSERT INTO agent_sessions (id, conversation_id, agent_handle, harness,"
        " resume_token, told, created_at, updated_at)"
        " VALUES ($1, $2, 'cheese', 'claude-code', 'october-session',"
        ' \'{"roster": "x"}\'::json, now(), now())',
        uuid.uuid4(),
        conversation,
    )


def _read_from(db: ReplayDatabase, conversation: uuid.UUID, at: datetime, block):
    db.execute(
        "INSERT INTO agent_reads (conversation_id, agent_handle, read_through_at,"
        " read_through_block_id, updated_at) VALUES ($1, $2, $3, $4, now())",
        conversation,
        SEAT,
        at,
        block,
    )


def _handed(db: ReplayDatabase, conversation: uuid.UUID) -> list[str]:
    """What a turn of the seat in ``conversation`` is handed now: everything
    said after its read position, as the turn reads it (`_read_window`)."""

    async def read() -> list[str]:
        engine = create_async_engine(db.url)
        try:
            async with async_sessionmaker(engine)() as session:
                cursor = await AgentSessionService(session).read_cursor(
                    conversation, SEAT
                )
                window = await inputs_after(
                    session, conversation, after=cursor, limit=40
                )
                return [block.content for block in window]
        finally:
            await engine.dispose()

    return asyncio.run(read())


def _private_chat(db: ReplayDatabase, project: uuid.UUID) -> uuid.UUID:
    chat = uuid.uuid4()
    db.execute(
        "INSERT INTO topics (id, project_id, title, kind, status, is_private,"
        " created_at, updated_at)"
        " VALUES ($1, $2, '私聊', 'topic', 'active', true, now(), now())",
        chat,
        project,
    )
    return chat


def test_the_first_call_in_an_old_main_line_reads_only_what_came_after():
    october = datetime(2026, 10, 1, tzinfo=UTC)
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        _seat(db, room)
        first = _say(db, project, room, "十月初的一句", at=october)
        _read_from(db, room, october, first)
        for day in range(1, 4):
            _say(
                db,
                project,
                room,
                f"@芝士 十月第 {day} 个问题",
                at=october + timedelta(days=day * 7),
                meta={"agent_recipient": {"handle": SEAT, "mentioned": True}},
            )
        _say(db, project, room, "大家周五见", at=october + timedelta(days=30))
        _session(db, room)
        chat = _private_chat(db, project)
        said_in_chat = _say(db, project, chat, "私聊里的一句", at=october)
        _read_from(db, chat, october - timedelta(days=1), said_in_chat)
        _session(db, chat)

        db.upgrade(AFTER)

        # Said after the release, and only that, is what the first turn reads.
        _say(db, project, room, "@芝士 新的问题", at=datetime.now(UTC))
        assert _handed(db, room) == ["@芝士 新的问题"]
        # It opens a new conversation, with the whole opening.
        session = db.fetchrow(
            "SELECT resume_token, told FROM agent_sessions WHERE conversation_id = $1",
            room,
        )
        assert session["resume_token"] is None and session["told"] is None

        # A private chat is not a channel: it reads on from where it was.
        assert _handed(db, chat) == ["私聊里的一句"]
        assert (
            db.fetchval(
                "SELECT resume_token FROM agent_sessions WHERE conversation_id = $1",
                chat,
            )
            == "october-session"
        )
