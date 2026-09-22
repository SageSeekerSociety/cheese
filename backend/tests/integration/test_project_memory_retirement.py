"""Old project memories survive in a document or an exact backup before deletion."""

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.integration.test_what_everyone_sees_is_a_document import (
    _legacy_memory,
    _run_migration,
)


@pytest.fixture
def retirement(monkeypatch, tmp_path):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/e7b2c49d8106_retire_the_project_memory_pool.py"
    )
    spec = importlib.util.spec_from_file_location("_retire_project_pool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    return module


async def _upgrade(session, module):
    def apply(connection):
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()

    await (await session.connection()).run_sync(apply)


async def _rows(session):
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT row_to_json(m) FROM memory_entries m "
                    "WHERE scope = 'project' ORDER BY id"
                )
            )
        ).scalars()
    )


def test_window_rows_land_retired_rows_stay_out_and_paragraph_anchors_survive(
    db_session, _portal, retirement, tmp_path
):
    async def run():
        project = await ProjectService(db_session).create(
            name="Migration test", owner_handle="owner", forge_kind="github_app"
        )
        topics = TopicService(db_session)
        await topics.edit_doc(
            topic_id=project.root_topic_id,
            content="## Goal\n\nKeep this paragraph.",
            author="owner",
            expected_version=0,
        )
        old_nodes = await BlockRepository(db_session).list_doc_nodes(
            project.root_topic_id
        )
        old_ids = {node.content: node.id for node in old_nodes}
        await _legacy_memory(db_session, project.id, "First live fact.")
        await _legacy_memory(db_session, project.id, "Obsolete fact.")
        await db_session.execute(
            sa.text(
                "UPDATE memory_entries SET retired_at = now() "
                "WHERE scope_id = :pool AND content = 'Obsolete fact.'"
            ),
            {"pool": str(project.id)},
        )
        await _run_migration(db_session)
        await _legacy_memory(
            db_session,
            project.id,
            "A fact from the deployment window. 中文\nSecond line.",
        )
        before = await _rows(db_session)

        await _upgrade(db_session, retirement)

        assert await _rows(db_session) == []
        backups = list(tmp_path.rglob("*.json"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text()) == before
        assert backups[0].stem == hashlib.sha256(backups[0].read_bytes()).hexdigest()
        assert backups[0].stat().st_mode & 0o777 == 0o600
        db_session.expire_all()
        await db_session.refresh(project)
        doc = await topics.get_doc(project.root_topic_id)
        assert doc.content.count("First live fact.") == 1
        assert "A fact from the deployment window. 中文\nSecond line." in doc.content
        assert "Obsolete fact." not in doc.content
        nodes = await BlockRepository(db_session).list_doc_nodes(project.root_topic_id)
        assert "First live fact." in "\n".join(node.content for node in nodes)
        assert "A fact from the deployment window." in "\n".join(
            node.content for node in nodes
        )
        assert all(
            any(node.id == id_ and node.content == content for node in nodes)
            for content, id_ in old_ids.items()
        )
        await _upgrade(db_session, retirement)
        assert list(tmp_path.rglob("*.json")) == backups

    _portal.call(run)


def test_an_unmatched_row_aborts_without_deleting_any_rows(
    db_session, _portal, retirement
):
    async def run():
        await _legacy_memory(db_session, uuid.uuid4(), "No project owns this row.")
        before = await _rows(db_session)
        with pytest.raises(RuntimeError, match="missing from their documents"):
            async with db_session.begin_nested():
                await _upgrade(db_session, retirement)
        assert await _rows(db_session) == before

    _portal.call(run)


def test_a_copy_with_different_content_cannot_authorize_deletion(
    db_session, _portal, retirement, monkeypatch
):
    async def run():
        project = await ProjectService(db_session).create(
            name="Copy test", owner_handle="owner", forge_kind="github_app"
        )
        await TopicService(db_session).edit_doc(
            topic_id=project.root_topic_id,
            content="Fact with the wrong value: 12.",
            author="owner",
            expected_version=0,
        )
        await _legacy_memory(db_session, project.id, "Fact with the right value: 21.")
        before = await _rows(db_session)
        monkeypatch.setattr(retirement, "copy_live_rows", lambda bind: None)
        with pytest.raises(RuntimeError, match="missing from their documents"):
            async with db_session.begin_nested():
                await _upgrade(db_session, retirement)
        assert await _rows(db_session) == before

    _portal.call(run)


def test_backup_failure_leaves_retired_rows_in_the_database(
    db_session, _portal, retirement, tmp_path
):
    async def run():
        await _legacy_memory(db_session, uuid.uuid4(), "Retired but still recoverable.")
        await db_session.execute(
            sa.text(
                "UPDATE memory_entries SET retired_at = now() WHERE scope = 'project'"
            )
        )
        before = await _rows(db_session)
        (tmp_path / "memory-migration").write_text(
            "A file blocks the backup directory."
        )
        with pytest.raises(OSError):
            async with db_session.begin_nested():
                await _upgrade(db_session, retirement)
        assert await _rows(db_session) == before

    _portal.call(run)


def test_a_corrupt_backup_is_not_overwritten_or_accepted(retirement):
    rows = [{"id": "old-row", "content": "keep this"}]
    backup = retirement.backup_rows(rows)
    backup.write_text("corrupt")
    with pytest.raises(RuntimeError, match="backup does not match"):
        retirement.backup_rows(rows)
    assert backup.read_text() == "corrupt"


def test_a_later_migration_failure_rolls_back_rows_and_document_nodes(
    db_session, _portal, retirement
):
    async def run():
        project = await ProjectService(db_session).create(
            name="Rollback test", owner_handle="owner", forge_kind="github_app"
        )
        topic_id = project.root_topic_id
        topics = TopicService(db_session)
        await topics.edit_doc(
            topic_id=topic_id,
            content="Original document.",
            author="owner",
            expected_version=0,
        )
        await _legacy_memory(db_session, project.id, "A pending migration fact.")
        before = await _rows(db_session)
        with pytest.raises(RuntimeError, match="later migration failed"):
            async with db_session.begin_nested():
                await _upgrade(db_session, retirement)
                raise RuntimeError("later migration failed")
        assert await _rows(db_session) == before
        db_session.expire_all()
        doc = await topics.get_doc(topic_id)
        assert doc.content == "Original document."
        nodes = await BlockRepository(db_session).list_doc_nodes(topic_id)
        assert [node.content for node in nodes] == ["Original document."]

    _portal.call(run)
