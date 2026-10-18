"""The four repositories moved onto ``Repository`` read exactly as before.

Each case keeps the hand-written read the repository had before it moved onto
``app.domain.repositories.Repository`` (``select(Model).where(Model.id == x,
*filters)`` and ``reads.get_by_ids(session, Model, ids, *filters)``) and
checks that the inherited ``get_by_id`` / ``get_by_ids`` return the same rows,
with the same number of SELECTs, for live, soft-deleted, missing, and repeated
ids. Dropping a repository's ``base_statement`` filter makes its case fail.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import ColumnElement, Table, event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.domain.avatars.models import Avatar
from app.domain.avatars.repositories import AvatarRepository
from app.domain.comments.models import Comment
from app.domain.comments.repositories import CommentRepository
from app.domain.groups.models import Group
from app.domain.groups.repositories import GroupRepository
from app.domain.reads import get_by_ids
from app.domain.repositories import Repository
from app.domain.tag.models import Tag
from app.domain.tag.repositories import TagRepository

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Case:
    repository: type[Repository[Any]]
    model: Any
    filters: Callable[[], Sequence[ColumnElement[bool]]]
    rows: Callable[[], list[Any]]
    asked: list[int]


def _comment(row_id: int, deleted_at: datetime | None) -> Comment:
    return Comment(
        id=row_id,
        commentable_type="QUESTION",
        commentable_id=1,
        content=f"c{row_id}",
        created_by_id=7,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=deleted_at,
    )


def _avatar(row_id: int) -> Avatar:
    return Avatar(
        id=row_id,
        url=f"https://example.invalid/{row_id}.png",
        name=f"a{row_id}",
        avatar_type="predefined",
        created_at=NOW,
        usage_count=0,
    )


CASES = {
    "tag": Case(
        TagRepository,
        Tag,
        lambda: (Tag.deleted_at.is_(None),),
        lambda: [
            Tag(id=1, name="python", created_by_id=10, created_at=NOW),
            Tag(id=2, name="gone", created_by_id=10, created_at=NOW, deleted_at=NOW),
            Tag(id=3, name="rust", created_by_id=11, created_at=NOW),
        ],
        [3, 1, 2, 404, 1],
    ),
    "avatar": Case(
        AvatarRepository,
        Avatar,
        lambda: (),
        lambda: [_avatar(10), _avatar(20)],
        [20, 404, 10, 20],
    ),
    "comment": Case(
        CommentRepository,
        Comment,
        lambda: (Comment.deleted_at.is_(None),),
        lambda: [_comment(101, None), _comment(102, NOW), _comment(103, None)],
        [102, 101, 888, 103, 101],
    ),
    "group": Case(
        GroupRepository,
        Group,
        lambda: (Group.deleted_at.is_(None),),
        lambda: [
            Group(id=5, name="g5", created_at=NOW, updated_at=NOW),
            Group(id=6, name="g6", created_at=NOW, updated_at=NOW, deleted_at=NOW),
            Group(id=7, name="g7", created_at=NOW, updated_at=NOW),
        ],
        [6, 7, 5, 999, 7],
    ),
}


@pytest.fixture
async def seeded(request):
    case: Case = CASES[request.param]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(cast(Table, case.model.__table__).create)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add_all(case.rows())
            await session.commit()
            yield case, session, engine
    finally:
        await engine.dispose()


async def _counting(
    engine: AsyncEngine, call: Callable[[], Awaitable[Any]]
) -> tuple[Any, int]:
    selects = 0

    def _on_execute(_conn, _cursor, statement: str, *_rest) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
    try:
        result = await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)
    return result, selects


@pytest.mark.parametrize("seeded", list(CASES), indirect=True)
async def test_get_by_id_reads_what_the_hand_read_did(seeded) -> None:
    case, session, engine = seeded
    repo = case.repository(session)

    for row_id in case.asked:

        async def _hand(row_id: int = row_id) -> Any:
            stmt = select(case.model).where(case.model.id == row_id, *case.filters())
            return (await session.execute(stmt)).scalar_one_or_none()

        hand, hand_selects = await _counting(engine, _hand)
        based, based_selects = await _counting(
            engine, lambda row_id=row_id: repo.get_by_id(row_id)
        )
        assert based is hand, row_id
        assert based_selects == hand_selects == 1


@pytest.mark.parametrize("seeded", list(CASES), indirect=True)
async def test_get_by_ids_reads_what_the_hand_read_did(seeded) -> None:
    case, session, engine = seeded
    repo = case.repository(session)

    for asked in (case.asked, []):
        hand, hand_selects = await _counting(
            engine,
            lambda asked=asked: get_by_ids(session, case.model, asked, *case.filters()),
        )
        based, based_selects = await _counting(
            engine, lambda asked=asked: repo.get_by_ids(asked)
        )
        assert list(based) == list(hand)
        assert all(based[key] is hand[key] for key in hand)
        assert based_selects == hand_selects == (1 if asked else 0)
