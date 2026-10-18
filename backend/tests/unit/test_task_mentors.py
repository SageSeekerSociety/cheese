"""set_mentors checks a whole mentor list in two reads (#3830).

Real SELECTs run against an in-memory copy of the two tables it reads, so the
query count below is what the code sends, not what a mock was told to expect.
"""

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.errors import BadRequestError
from app.domain.space.models import SpaceAdminRelation
from app.domain.task import mentors
from app.domain.user.models import User

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SPACE_ID = 5
CREATOR_ID = 1


@pytest.fixture
async def mentor_sql():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    for model in (User, SpaceAdminRelation):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            # Only the test's copied schema loses PostgreSQL sequences.
            column.server_default = None
            column.default = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for user_id, handle in ((1, "teacher"), (2, "ta"), (3, "student")):
                session.add(
                    User(
                        id=user_id,
                        username=handle,
                        email=f"{handle}@example.com",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            for relation_id, user_id in ((1, 1), (2, 2)):
                session.add(
                    SpaceAdminRelation(
                        id=relation_id,
                        space_id=SPACE_ID,
                        user_id=user_id,
                        role=1,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            await session.commit()
            yield session, engine
    finally:
        await engine.dispose()


def _task(space_id: int | None = SPACE_ID):
    return SimpleNamespace(id=42, space_id=space_id)


async def _set(session, handles, *, space_id: int | None = SPACE_ID):
    replace = AsyncMock()
    with patch.object(mentors.TaskMentorRepository, "replace", replace):
        await mentors.set_mentors(
            session,
            task=_task(space_id),
            handles=handles,
            creator_user_id=CREATOR_ID,
        )
    return replace


@pytest.mark.anyio
async def test_unknown_handle_is_account_not_found(mentor_sql):
    session, _ = mentor_sql
    with pytest.raises(BadRequestError) as caught:
        await _set(session, ["ta", "nobody", "student"])
    message = caught.value.args[0]
    assert message.key == "accountNotFound"
    assert message.params == {"handle": "nobody"}
    assert caught.value.data is None


@pytest.mark.anyio
async def test_non_admin_is_refused(mentor_sql):
    session, _ = mentor_sql
    with pytest.raises(BadRequestError) as caught:
        await _set(session, ["ta", "student"])
    message = caught.value.args[0]
    assert message.key == "mentorNotSpaceAdmin"
    assert message.params == {"handle": "student"}


@pytest.mark.anyio
async def test_no_board_means_only_the_creator(mentor_sql):
    session, _ = mentor_sql
    replace = await _set(session, ["teacher"], space_id=None)
    replace.assert_awaited_once_with(42, ["teacher"])
    with pytest.raises(BadRequestError) as caught:
        await _set(session, ["ta"], space_id=None)
    assert caught.value.args[0].key == "mentorNotSpaceAdmin"


@pytest.mark.anyio
async def test_admins_are_saved_in_the_order_given(mentor_sql):
    session, _ = mentor_sql
    replace = await _set(session, ["ta", "teacher", "ta"])
    replace.assert_awaited_once_with(42, ["ta", "teacher", "ta"])


@pytest.mark.anyio
async def test_each_table_is_read_once(mentor_sql):
    session, engine = mentor_sql
    statements: list[str] = []

    def count_select(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_select)
    try:
        await _set(session, ["ta", "teacher", "ta"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_select)

    def reads(table: str) -> int:
        pattern = re.compile(rf'\bFROM "?{table}"?(\s|$)')
        return sum(bool(pattern.search(s)) for s in statements)

    assert reads("user") == 1, statements
    assert reads("space_admin_relation") == 1, statements
    assert len(statements) == 2, statements
