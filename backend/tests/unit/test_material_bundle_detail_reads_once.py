"""A bundle's detail reads the material table once, in the bundle's own order.

`get_bundle_detail` used to call `get_by_id` per id: a bundle of N materials
cost N SELECTs. `MaterialRepository.get_by_ids` reads them all in one
statement, and the service then walks `material_ids` — the order the bundle's
relation table gives — skipping ids that name nothing, exactly as the per-id
loop did.

Both halves are pinned because either can regress on its own. The count says
one statement where the loop made N. The order says `material_ids`, not the
order the database happened to return: SQLite hands rows back by row id, so a
reader that took that order would list 2 before 3 even though the bundle says
3 first, and would quietly reorder a teacher's arrangement.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.domain.materials.models import (
    Material,
    MaterialBundle,
    MaterialBundleRelation,
)
from app.domain.materials.repositories import (
    MaterialBundleRepository,
    MaterialRepository,
)
from app.domain.materials.services import MaterialBundleService

NOW = datetime(2026, 1, 1, tzinfo=UTC)

# `material` but not `material_bundle` or `materialbundles_relation`: after
# "material" those names continue with "_", which is a word character, so \b
# does not match there.
_MATERIAL_FROM = re.compile(r"\bFROM\s+material\b", re.IGNORECASE)


@pytest.fixture
async def bundle_engine():
    """A bundle over three materials, related in the order 3, 1, 2.

    Material 1 is deleted before the fixture is handed out. Nothing enforces a
    foreign key between the relation table and the material table, so the
    relation row outlives the material row — that is what "a bundle names a
    material that is gone" looks like in this schema.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    for model in (Material, MaterialBundle, MaterialBundleRelation):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            # Only the test's copied schema loses PostgreSQL defaults.
            column.server_default = None
            column.default = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add_all(
                Material(
                    id=material_id,
                    type="PDF",
                    url=f"/files/{material_id}.pdf",
                    name=f"material-{material_id}",
                    uploader_id=9,
                    created_at=NOW,
                    expires=None,
                    download_count=0,
                    meta={},
                )
                for material_id in (1, 2, 3)
            )
            session.add(
                MaterialBundle(
                    id=1,
                    title="A bundle",
                    content="content",
                    creator_id=42,
                    created_at=NOW,
                    updated_at=NOW,
                    rating=0,
                    rating_count=0,
                    my_rating=None,
                    comments_count=0,
                )
            )
            await session.flush()
            session.add_all(
                MaterialBundleRelation(material_id=material_id, bundle_id=1)
                for material_id in (3, 1, 2)
            )
            await session.flush()
            await session.delete(await session.get(Material, 1))
            await session.commit()
            yield engine, session
    finally:
        await engine.dispose()


def _service(session: AsyncSession) -> MaterialBundleService:
    return MaterialBundleService(
        repo=MaterialBundleRepository(session),
        material_repo=MaterialRepository(session),
    )


async def _material_selects_during(
    engine: AsyncEngine, call: Callable[[], Awaitable[Any]]
) -> tuple[Any, list[str]]:
    """Run `call` and collect the SELECTs that read the material table."""
    statements: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if _MATERIAL_FROM.search(statement):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        result = await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    return result, statements


async def test_reads_the_material_table_once_for_the_whole_bundle(bundle_engine):
    engine, session = bundle_engine

    _, statements = await _material_selects_during(
        engine, lambda: _service(session).get_bundle_detail(1)
    )

    assert len(statements) == 1, statements


async def test_get_by_ids_is_keyed_by_id_and_omits_what_is_gone(bundle_engine):
    _engine, session = bundle_engine

    found = await MaterialRepository(session).get_by_ids([1, 2, 3])

    assert set(found) == {2, 3}
    assert found[2].name == "material-2"


async def test_lists_materials_in_the_order_the_bundle_names_them(bundle_engine):
    _engine, session = bundle_engine
    relation_ids = await MaterialBundleRepository(session).get_materials_for_bundle(1)

    dto = await _service(session).get_bundle_detail(1)

    # The fixture lists 3 before 1 and 2; a reader that took the database's
    # own order would come back ascending instead.
    assert relation_ids == [3, 1, 2]
    assert [material["id"] for material in dto["materials"]] == [3, 2]


async def test_a_material_that_is_gone_is_left_out(bundle_engine):
    _engine, session = bundle_engine

    dto = await _service(session).get_bundle_detail(1)

    assert [material["name"] for material in dto["materials"]] == [
        "material-3",
        "material-2",
    ]
    assert dto["creator"] == {"id": 42}
