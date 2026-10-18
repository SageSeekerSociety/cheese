"""One page of real-name access records reads its accessors with one user query."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.domain.user.models import User
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import UserRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _log(accessor_id: int, log_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=log_id,
        accessor_id=accessor_id,
        module_type=None,
        module_entity_id=None,
        access_type="VIEW",
        access_reason="audit",
        created_at=NOW,
    )


@pytest.mark.anyio
async def test_access_log_page_reads_user_table_once() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    table = User.__table__.to_metadata(metadata)
    for column in table.columns:
        column.server_default = None
        column.default = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for uid in (1, 2, 3):
                session.add(
                    User(
                        id=uid,
                        username=f"u{uid}",
                        email=f"u{uid}@example.com",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            await session.flush()

            rows = [_log(2, 1), _log(3, 2), _log(2, 3)]
            realname_repo = AsyncMock()
            realname_repo.list_access_logs.return_value = (rows, 3)
            profile_repo = AsyncMock()
            profile_repo.get_profiles_by_user_ids.return_value = {
                uid: SimpleNamespace(nickname=f"n{uid}", intro="") for uid in (2, 3)
            }
            profile_repo.chosen_avatar_ids.return_value = {}
            space_labels = AsyncMock()
            space_labels.describe.return_value = {}
            service = UserRealNameService(
                session=session,
                user_repo=UserRepository(session),
                profile_repo=profile_repo,
                realname_repo=realname_repo,
                space_labels=space_labels,
            )

            statements: list[str] = []

            def count_user_reads(_conn, _cursor, statement, *_rest) -> None:
                if statement.lstrip().upper().startswith("SELECT") and (
                    'FROM "user"' in statement or "FROM user" in statement
                ):
                    statements.append(statement)

            event.listen(engine.sync_engine, "before_cursor_execute", count_user_reads)
            try:
                logs, _page = await service.get_access_logs(
                    target_user_id=1, page_size=20, page_start=0
                )
            finally:
                event.remove(
                    engine.sync_engine, "before_cursor_execute", count_user_reads
                )

            # `_ensure_user_exists(target)` is one read; the accessors must be one more.
            assert len(statements) == 2
            assert [log["accessor"]["nickname"] for log in logs] == ["n2", "n3", "n2"]
    finally:
        await engine.dispose()
