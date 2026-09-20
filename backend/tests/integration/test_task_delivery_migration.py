"""Exercise the forward migration against populated PostgreSQL history."""

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import settings


def load_migration():
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/d7a419be028c_tasks_own_delivery.py"
    )
    spec = importlib.util.spec_from_file_location("task_delivery_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("unmapped_active", [False, True])
def test_migrate_shared_delivery_without_inventing_independent_history(
    db_session, _portal, tmp_path, monkeypatch, unmapped_active
):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    migration = load_migration()

    async def run():
        connection = await db_session.connection()

        def check(conn):
            # An isolated schema retains the current task table's real column
            # types/defaults while restoring only the previous delivery layout.
            schema = "task_migration_" + uuid.uuid4().hex
            conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
            conn.execute(sa.text(f"SET LOCAL search_path TO {schema}, public"))
            conn.execute(
                sa.text("CREATE TABLE tasks (LIKE public.tasks INCLUDING ALL)")
            )
            for column in (
                "branch_name",
                "workspace_name",
                "base_branch",
                "pr_url",
                "pr_number",
                "delivered_head",
                "last_check_at",
                "last_check_ok",
                "last_check_detail",
                "historical_claimed_paths",
                "historical_delivery",
                "base_task_id",
                "historical_delivery_id",
            ):
                conn.execute(sa.text(f"ALTER TABLE tasks DROP COLUMN {column} CASCADE"))
            conn.execute(sa.text("ALTER TABLE tasks ADD COLUMN tree_id uuid NOT NULL"))
            conn.execute(
                sa.text(
                    "ALTER TABLE tasks ADD COLUMN claimed_paths "
                    "text[] NOT NULL DEFAULT '{}'"
                )
            )
            for ddl in (
                "CREATE TABLE topics "
                "(id uuid PRIMARY KEY, project_id uuid, title text)",
                """CREATE TABLE work_trees (
                    id uuid PRIMARY KEY, project_id uuid, room_id uuid, status text,
                    created_at timestamptz, updated_at timestamptz,
                    merged_at timestamptz, sealed_at timestamptz,
                    pr_number int, pr_url text, delivered_head text,
                    last_check_at timestamptz, last_check_ok boolean,
                    last_check_detail text)""",
                """CREATE TABLE accept_cards (
                    id uuid PRIMARY KEY, task_id uuid, tree_id uuid, status text,
                    change_subject text, reviewer_handle text,
                    decided_by text, created_at timestamptz)""",
                "CREATE TABLE room_locks (kind text)",
            ):
                conn.execute(sa.text(ddl))
            project, room, open_tree, merged_tree, first, second = [
                uuid.uuid4() for _ in range(6)
            ]
            conn.execute(
                sa.text("INSERT INTO topics VALUES (:room, :project, 'Room')"),
                {"room": room, "project": project},
            )
            for tree, task, status, pr in [
                (open_tree, first, "open", 714),
                (merged_tree, second, "merged", 700),
            ]:
                values = {
                    "tree": tree,
                    "task": task,
                    "room": room,
                    "project": project,
                    "status": status,
                    "pr": pr,
                }
                conn.execute(
                    sa.text("""INSERT INTO work_trees VALUES (
                    :tree, :project, :room, :status, now(), now(),
                    CASE WHEN :status='merged' THEN now() END,
                    NULL, :pr, 'https://example/pull',
                    'retained-head', now(), true, 'original check')"""),
                    values,
                )
                conn.execute(
                    sa.text("""INSERT INTO tasks (
                    id, project_id, room_id, title, status, created_at, updated_at,
                    tree_id, claimed_paths, brief, contributor_handles)
                    VALUES (:task, :project, :room, 'Original task', 'open',
                            now(), now(), :tree, ARRAY['src/shared.py'],
                            'Original brief', '["alice"]')"""),
                    values,
                )
                conn.execute(
                    sa.text("""INSERT INTO accept_cards VALUES (
                    :task, :task, :tree,
                    CASE WHEN :status='merged' THEN 'accepted' ELSE 'pending' END,
                    'feat: retained delivery', 'reviewer', 'approver', now())"""),
                    values,
                )
            conn.execute(sa.text("INSERT INTO room_locks VALUES ('file'), ('heavy')"))
            if unmapped_active:
                conn.execute(
                    sa.text("""INSERT INTO accept_cards (id, status, created_at)
                    VALUES (:id, 'pending', now())"""),
                    {"id": uuid.uuid4()},
                )
            migration.op = Operations(MigrationContext.configure(conn))
            if unmapped_active:
                with pytest.raises(sa.exc.DBAPIError, match="lack a delivery branch"):
                    with conn.begin_nested():
                        migration.upgrade()
                assert (
                    conn.execute(
                        sa.text("SELECT count(*) FROM work_trees")
                    ).scalar_one()
                    == 2
                )
                assert (
                    conn.execute(sa.text("SELECT count(*) FROM tasks")).scalar_one()
                    == 2
                )
            else:
                migration.upgrade()
                rows = {
                    r["id"]: r
                    for r in conn.execute(sa.text("SELECT * FROM tasks")).mappings()
                }
                assert len(rows) == 4
                assert rows[first]["historical_delivery_id"] == open_tree
                assert rows[first]["historical_claimed_paths"] == ["src/shared.py"]
                assert rows[first]["brief"] == "Original brief"
                assert rows[first]["status"] == rows[second]["status"] == "closed"
                assert rows[open_tree]["branch_name"] == f"topic/{open_tree.hex[:8]}"
                assert rows[open_tree]["pr_number"] == 714
                assert rows[open_tree]["reviewer_handle"] == "reviewer"
                assert (
                    rows[open_tree]["historical_delivery"]["delivered_head"]
                    == "retained-head"
                )
                assert rows[merged_tree]["accepted_by"] == "approver"
                assert rows[merged_tree]["accepted_at"] is not None
                assert (
                    conn.execute(
                        sa.text("SELECT task_id FROM accept_cards WHERE id=:id"),
                        {"id": first},
                    ).scalar_one()
                    == open_tree
                )
                assert conn.execute(
                    sa.text("SELECT kind FROM room_locks")
                ).scalars().all() == ["heavy"]
            conn.execute(sa.text("SET LOCAL search_path TO public"))
            conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))

        await connection.run_sync(check)

    _portal.call(run)
