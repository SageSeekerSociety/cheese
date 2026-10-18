"""Unit tests for ``app.domain.reads.get_by_ids`` and its four exemplar repositories.

Verifies that ``TagRepository.get_by_ids``, ``AvatarRepository.get_by_ids``,
``CommentRepository.get_by_ids``, and ``GroupRepository.get_by_ids`` each:

1. Issue exactly 1 SQL ``SELECT`` when given ``N`` ids (including duplicates,
   non-existent ids, and soft-deleted rows) and 0 SQL queries when given an
   empty sequence.
2. Deduplicate bound ``IN`` parameters before hitting the database.
3. Return the exact same ``{id: row}`` mapping as calling ``get_by_id`` one id
   at a time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import Table, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.domain import reads
from app.domain.avatars.models import Avatar
from app.domain.avatars.repositories import AvatarRepository
from app.domain.comments.models import Comment
from app.domain.comments.repositories import CommentRepository
from app.domain.groups.models import Group
from app.domain.groups.repositories import GroupRepository
from app.domain.reads import get_by_ids
from app.domain.tag.models import Tag
from app.domain.tag.repositories import TagRepository

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
async def sqlite_session():
    """In-memory SQLite engine and session with Tag, Avatar, Comment, and Group tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            for model in (Tag, Avatar, Comment, Group):
                await conn.run_sync(cast(Table, model.__table__).create)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session, engine
    finally:
        await engine.dispose()


async def _capture_selects(
    engine: AsyncEngine,
    call: Callable[[], Awaitable[Any]],
) -> tuple[Any, list[tuple[str, Any]]]:
    executed: list[tuple[str, Any]] = []

    def _on_execute(_conn, _cursor, statement: str, parameters: Any, *_rest) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            executed.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
    try:
        result = await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)
    return result, executed


def _bound_values(parameters: Any) -> list[Any]:
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return list(parameters)
    return [parameters]


def test_reads_module_documents_batch_sibling_requirement() -> None:
    doc = reads.__doc__ or ""
    assert "get_by_id" in doc
    assert "get_by_ids" in doc


async def test_get_by_ids_empty_input_issues_zero_queries(sqlite_session) -> None:
    session, engine = sqlite_session
    session.add(Tag(id=1, name="python", created_by_id=10, created_at=NOW))
    await session.commit()

    repo = TagRepository(session)
    found, queries = await _capture_selects(engine, lambda: repo.get_by_ids([]))
    assert found == {}
    assert len(queries) == 0

    gen_found, gen_queries = await _capture_selects(
        engine, lambda: get_by_ids(session, Tag, (i for i in ()))
    )
    assert gen_found == {}
    assert len(gen_queries) == 0


async def test_tag_repository_get_by_ids_batches_in_one_query_and_matches_get_by_id(
    sqlite_session,
) -> None:
    session, engine = sqlite_session
    session.add_all(
        [
            Tag(id=1, name="python", created_by_id=10, created_at=NOW, deleted_at=None),
            Tag(id=2, name="rust", created_by_id=10, created_at=NOW, deleted_at=None),
            Tag(
                id=3,
                name="deleted-tag",
                created_by_id=10,
                created_at=NOW,
                deleted_at=NOW,
            ),
            Tag(id=4, name="go", created_by_id=11, created_at=NOW, deleted_at=None),
        ]
    )
    await session.commit()

    repo = TagRepository(session)
    asked_ids = [4, 1, 999, 1, 3, 2, 4]

    batch_result, batch_queries = await _capture_selects(
        engine, lambda: repo.get_by_ids(asked_ids)
    )
    assert len(batch_queries) == 1
    assert _bound_values(batch_queries[0][1]) == [4, 1, 999, 3, 2]

    async def _loop_by_id() -> dict[int, Tag]:
        out: dict[int, Tag] = {}
        for tag_id in asked_ids:
            row = await repo.get_by_id(tag_id)
            if row is not None:
                out[tag_id] = row
        return out

    single_result, single_queries = await _capture_selects(engine, _loop_by_id)
    assert len(single_queries) == len(asked_ids)
    assert set(batch_result.keys()) == {1, 2, 4}
    assert batch_result.keys() == single_result.keys()
    for tag_id, row in batch_result.items():
        assert (row.id, row.name, row.created_by_id, row.deleted_at) == (
            single_result[tag_id].id,
            single_result[tag_id].name,
            single_result[tag_id].created_by_id,
            single_result[tag_id].deleted_at,
        )


async def test_avatar_repository_get_by_ids_batches_in_one_query_and_matches_get_by_id(
    sqlite_session,
) -> None:
    session, engine = sqlite_session
    session.add_all(
        [
            Avatar(
                id=10,
                url="https://example.invalid/a1.png",
                name="a1",
                avatar_type="default",
                created_at=NOW,
                usage_count=1,
            ),
            Avatar(
                id=20,
                url="https://example.invalid/a2.png",
                name="a2",
                avatar_type="predefined",
                created_at=NOW,
                usage_count=5,
            ),
            Avatar(
                id=30,
                url="https://example.invalid/a3.png",
                name="a3",
                avatar_type="upload",
                created_at=NOW,
                usage_count=0,
            ),
        ]
    )
    await session.commit()

    repo = AvatarRepository(session)
    asked_ids = [20, 10, 404, 20, 30, 10]

    batch_result, batch_queries = await _capture_selects(
        engine, lambda: repo.get_by_ids(asked_ids)
    )
    assert len(batch_queries) == 1
    assert _bound_values(batch_queries[0][1]) == [20, 10, 404, 30]

    async def _loop_by_id() -> dict[int, Avatar]:
        out: dict[int, Avatar] = {}
        for avatar_id in asked_ids:
            row = await repo.get_by_id(avatar_id)
            if row is not None:
                out[avatar_id] = row
        return out

    single_result, single_queries = await _capture_selects(engine, _loop_by_id)
    assert len(single_queries) == len(asked_ids)
    assert set(batch_result.keys()) == {10, 20, 30}
    assert batch_result.keys() == single_result.keys()
    for avatar_id, row in batch_result.items():
        assert (row.id, row.url, row.name, row.avatar_type, row.usage_count) == (
            single_result[avatar_id].id,
            single_result[avatar_id].url,
            single_result[avatar_id].name,
            single_result[avatar_id].avatar_type,
            single_result[avatar_id].usage_count,
        )


async def test_comment_repository_get_by_ids_batches_in_one_query_and_matches_get_by_id(
    sqlite_session,
) -> None:
    session, engine = sqlite_session
    session.add_all(
        [
            Comment(
                id=101,
                commentable_type="QUESTION",
                commentable_id=1,
                content="first",
                created_by_id=7,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            ),
            Comment(
                id=102,
                commentable_type="ANSWER",
                commentable_id=2,
                content="second",
                created_by_id=8,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            ),
            Comment(
                id=103,
                commentable_type="COMMENT",
                commentable_id=101,
                content="soft-deleted",
                created_by_id=9,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=NOW,
            ),
        ]
    )
    await session.commit()

    repo = CommentRepository(session)
    asked_ids = [102, 101, 103, 102, 888, 101]

    batch_result, batch_queries = await _capture_selects(
        engine, lambda: repo.get_by_ids(asked_ids)
    )
    assert len(batch_queries) == 1
    assert _bound_values(batch_queries[0][1]) == [102, 101, 103, 888]

    async def _loop_by_id() -> dict[int, Comment]:
        out: dict[int, Comment] = {}
        for comment_id in asked_ids:
            row = await repo.get_by_id(comment_id)
            if row is not None:
                out[comment_id] = row
        return out

    single_result, single_queries = await _capture_selects(engine, _loop_by_id)
    assert len(single_queries) == len(asked_ids)
    assert set(batch_result.keys()) == {101, 102}
    assert batch_result.keys() == single_result.keys()
    for comment_id, row in batch_result.items():
        assert (
            row.id,
            row.commentable_type,
            row.commentable_id,
            row.content,
            row.created_by_id,
            row.deleted_at,
        ) == (
            single_result[comment_id].id,
            single_result[comment_id].commentable_type,
            single_result[comment_id].commentable_id,
            single_result[comment_id].content,
            single_result[comment_id].created_by_id,
            single_result[comment_id].deleted_at,
        )


async def test_group_repository_get_by_ids_batches_in_one_query_and_matches_get_by_id(
    sqlite_session,
) -> None:
    session, engine = sqlite_session
    session.add_all(
        [
            Group(
                id=501, name="alpha", created_at=NOW, updated_at=NOW, deleted_at=None
            ),
            Group(id=502, name="beta", created_at=NOW, updated_at=NOW, deleted_at=None),
            Group(
                id=503,
                name="deleted-group",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=NOW,
            ),
            Group(
                id=504, name="gamma", created_at=NOW, updated_at=NOW, deleted_at=None
            ),
        ]
    )
    await session.commit()

    repo = GroupRepository(session)
    asked_ids = [504, 501, 503, 777, 501, 502, 504]

    batch_result, batch_queries = await _capture_selects(
        engine, lambda: repo.get_by_ids(asked_ids)
    )
    assert len(batch_queries) == 1
    assert _bound_values(batch_queries[0][1]) == [504, 501, 503, 777, 502]

    async def _loop_by_id() -> dict[int, Group]:
        out: dict[int, Group] = {}
        for group_id in asked_ids:
            row = await repo.get_by_id(group_id)
            if row is not None:
                out[group_id] = row
        return out

    single_result, single_queries = await _capture_selects(engine, _loop_by_id)
    assert len(single_queries) == len(asked_ids)
    assert set(batch_result.keys()) == {501, 502, 504}
    assert batch_result.keys() == single_result.keys()
    for group_id, row in batch_result.items():
        assert (row.id, row.name, row.deleted_at) == (
            single_result[group_id].id,
            single_result[group_id].name,
            single_result[group_id].deleted_at,
        )
