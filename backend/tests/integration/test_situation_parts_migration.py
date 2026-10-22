"""A 现状 written as Markdown is rewritten from records on the next open (c8e2f4a6b913).

The rule, as stated before the migration was written: a project's Markdown
card cannot be turned into checked parts, so after the upgrade it counts as
never written — no parts, no written time — and the next person to open the
overview gets one built from records instead of a blank card.
"""

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "a3c7e1f9d240"
AFTER = "c8e2f4a6b913"


def test_a_markdown_card_counts_as_never_written_after_the_upgrade() -> None:
    with database_at(BEFORE) as db:
        project, _ = seed_room(db)
        db.execute(
            "INSERT INTO project_situations (project_id, content, written_at,"
            " major, minor, model) VALUES ($1, '报名 {✓ 可用}：已上线', now(),"
            " '0|1|2026-10-20|None|2026-10-21', '2026-10-21', 'm')",
            project,
        )

        db.upgrade(AFTER)

        row = db.fetchrow(
            "SELECT parts, written_at FROM project_situations WHERE project_id = $1",
            project,
        )
        assert row["written_at"] is None
        assert row["parts"] == "[]"
