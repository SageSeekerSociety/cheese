"""What a pending migration changes under an owner still on an older image.

deploy/migration-touches.py decides whether a deploy closes the standing
connection owners' sockets: only a migration that drops, renames or alters
schema an owner maps does. Missing such a change leaves the owner failing every
query on it (#3411); reporting one that is not there closes every device link
and preview tunnel for nothing.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "migration-touches.py"
VERSIONS = Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions"


def touches(*sources: str) -> list[set[str]]:
    """What each migration touches, as the deploy reads it."""
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for index, source in enumerate(sources):
            path = Path(directory) / f"m{index}.py"
            path.write_text(source)
            paths.append(str(path))
        return read(paths)


def read(paths: list[str]) -> list[set[str]]:
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--files", *paths],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line.removeprefix("migration-touches ") for line in result.stdout.splitlines()]
    assert lines[-1] == "ok", result.stdout
    answer: list[set[str]] = []
    for line in lines[:-1]:
        if line.startswith("pending "):
            answer.append(set())
        else:
            answer[-1].add(line)
    return answer


HEAD = """
import sqlalchemy as sa
from alembic import op
"""


def migration(body: str) -> str:
    return HEAD + textwrap.dedent(body)


class MigrationTouchesTest(unittest.TestCase):
    def test_drops_renames_and_alters_are_touches(self):
        (found,) = touches(
            migration(
                """
            def upgrade():
                op.drop_table("room_locks")
                op.drop_column("topics", "compute_config")
                op.alter_column("tasks", "title", type_=sa.Text())
                op.rename_table("alerts", "notices")
                with op.batch_alter_table("blocks") as batch:
                    batch.drop_column("legacy")
                op.execute("ALTER TABLE agents RENAME COLUMN handle TO name")
                op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS last_ip, ADD COLUMN seen timestamptz")
                op.execute("DROP TABLE IF EXISTS old_a, old_b CASCADE")
            """)
        )
        self.assertEqual(
            found,
            {
                "room_locks",
                "topics.compute_config",
                "tasks.title",
                "alerts",
                "blocks.legacy",
                "agents.handle",
                "devices.last_ip",
                "old_a",
                "old_b",
            },
        )

    def test_additions_and_row_changes_touch_nothing(self):
        # An older model never selects a column it does not map, and an added
        # column is nullable or has a server default (.claude/rules/migrations.md).
        (found,) = touches(
            migration(
                """
            def upgrade():
                op.create_table("fresh", sa.Column("id", sa.Integer(), primary_key=True))
                op.add_column("topics", sa.Column("color", sa.Text(), nullable=True))
                op.create_index("ix_topics_color", "topics", ["color"])
                op.execute(sa.schema.CreateSequence(sa.Sequence("fresh_seq")))
                op.execute("ALTER TABLE topics ADD CONSTRAINT ck_color CHECK (color <> '') NOT VALID")
                op.execute("ALTER TABLE topics ALTER COLUMN color SET DEFAULT 'red'")
                op.execute("ALTER TABLE topics ALTER COLUMN owner_id DROP NOT NULL")
                op.execute(sa.text("UPDATE topics SET color = :c").bindparams(c="red"))
                for column in ("ADD COLUMN a text", "ADD COLUMN b text"):
                    op.execute(f"ALTER TABLE topics {column}")
                op.get_bind().execute(sa.table("topics").update().values(color="red"))
            """)
        )
        self.assertEqual(found, set())

    def test_names_through_loops_and_constants(self):
        (found,) = touches(
            migration(
                """
            OLD = ("compute_config", "compute_profile")

            def upgrade():
                for column in OLD:
                    op.drop_column("topics", column)
                for table, column in (("tasks", "a"), ("blocks", "b")):
                    op.drop_column(table, column)
            """)
        )
        self.assertEqual(found, {"topics.compute_config", "topics.compute_profile", "tasks.a", "blocks.b"})

    def test_what_cannot_be_read_touches_everything(self):
        for body in (
            "op.drop_column('topics', name_from_somewhere())",
            "op.execute(other_module.SQL)",
            "op.execute(f'ALTER TABLE topics {clause_from_a_parameter}')",
            "op.execute(PREFIX + ' WHERE true')",
        ):
            with self.subTest(body=body):
                (found,) = touches(HEAD + "\ndef upgrade():\n    " + body + "\n")
                self.assertIn("*", found)

    def test_schema_changes_it_does_not_parse_touch_everything(self):
        for body in (
            "op.execute(f'ALTER {kind} IF EXISTS \"{old}\" RENAME TO \"{new}\"')",
            "op.execute('ALTER TYPE task_status RENAME VALUE \\'open\\' TO \\'todo\\'')",
            "op.execute('DROP VIEW topic_summary')",
            "op.execute('DROP TYPE task_status')",
            "op.execute(\"DO $$ BEGIN EXECUTE 'ALTER TABLE topics DROP COLUMN x'; END $$\")",
            "_sibling('8b2f4d6a1c37_backfill_agent_environments').upgrade()",
        ):
            with self.subTest(body=body):
                (found,) = touches(HEAD + "\ndef upgrade():\n    " + body + "\n")
                self.assertIn("*", found)

    def test_dropped_indexes_and_constraints_and_new_required_columns_touch_their_table(self):
        # An older writer's ON CONFLICT names a unique index or constraint, and
        # its insert leaves a new NOT NULL column without a default empty.
        (found,) = touches(
            migration(
                """
            def upgrade():
                op.drop_index("uq_room_task", table_name="tasks")
                op.drop_constraint("uq_member", "members", type_="unique")
                op.add_column("topics", sa.Column("kind", sa.Text(), nullable=False))
                op.add_column("blocks", sa.Column("seq", sa.Integer(), nullable=False, server_default="0"))
                op.execute("ALTER TABLE devices ADD COLUMN owner uuid NOT NULL")
                op.execute("ALTER TABLE devices ALTER COLUMN name SET NOT NULL")
            """)
        )
        self.assertEqual(found, {"tasks", "members", "topics", "devices", "devices.name"})

    def test_migrations_that_read_as_nothing_but_change_tables(self):
        # Both rename tables through helpers or another migration's code.
        for revision in ("b5d90a17c3e2", "e7c2b41d90a5"):
            with self.subTest(revision=revision):
                (found,) = read([str(next(VERSIONS.glob(f"{revision}_*.py")))])
                self.assertIn("*", found)

    def test_downgrade_is_never_run_by_a_deploy(self):
        (found,) = touches(
            migration(
                """
            def upgrade():
                op.add_column("topics", sa.Column("color", sa.Text(), nullable=True))

            def downgrade():
                op.drop_column("topics", "color")
                op.execute(anything)
            """)
        )
        self.assertEqual(found, set())

    def test_the_migration_that_broke_the_owner_is_seen(self):
        # #3411: its drops left the dev device owner answering ~1500 executor
        # calls with 500 until it was released by hand.
        (found,) = read([str(next(VERSIONS.glob("c4e9a2f7d153_*.py")))])
        self.assertTrue(
            {"topics.compute_config", "topics.compute_profile", "tasks.compute_config", "cloud_host_homes"}
            <= found
        )


if __name__ == "__main__":
    unittest.main()
