"""Old spaces' auto-created category takes its owner's language (ad230848b32a).

The rules, as stated before the migration was written:

- a category still named ``General`` and described ``Auto generated default
  category`` was written by the platform and never changed, so it takes the
  name a space created today would get in its owner's language: 默认分类 for a
  Chinese-speaking owner or one who never picked a language, ``General`` with
  the current description for an English-speaking one;
- a category whose owner renamed it, or rewrote its description, keeps what
  they wrote — including one that kept ``General`` but has a description of
  their own;
- a deleted category is left alone.
"""

import uuid

import asyncpg

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "26ba2f15735b"
AFTER = "ad230848b32a"

GENERATED = ("General", "Auto generated default category")


def _seed(db: ReplayDatabase) -> dict[str, int]:
    """One space per case, each owned by a user in the given language.
    Returns the category id per case name."""
    cases = {
        "zh_owner": ("zh-CN", *GENERATED, False),
        "unset_owner": (None, *GENERATED, False),
        "en_owner": ("en", *GENERATED, False),
        "renamed": ("zh-CN", "课程作业", GENERATED[1], False),
        "own_description": ("zh-CN", "General", "放所有练习题", False),
        "deleted": ("zh-CN", *GENERATED, True),
    }
    ids: dict[str, int] = {}

    async def work(conn: asyncpg.Connection) -> None:
        for case, (language, name, description, deleted) in cases.items():
            n = uuid.uuid4().int % 1_000_000
            handle = f"owner-{case}-{n}"
            user_id = await conn.fetchval(
                'INSERT INTO "user" (username, email, language, created_at,'
                " updated_at) VALUES ($1::varchar, $1::varchar || '@example.test',"
                " $2, now(), now()) RETURNING id",
                handle,
                language,
            )
            space_id = 1_930_000_000 + n
            category_id = 1_940_000_000 + n
            await conn.execute(
                "INSERT INTO space (id, name, intro, description, enable_rank,"
                " default_category_id, task_templates, created_at, updated_at)"
                " VALUES ($1, $2, '', '', false, $3, '[]', now(), now())",
                space_id,
                f"space {case}",
                category_id,
            )
            await conn.execute(
                "INSERT INTO space_admin_relation (id, space_id, user_id, role,"
                " created_at, updated_at) VALUES ($1, $2, $3, 0, now(), now())",
                1_950_000_000 + n,
                space_id,
                user_id,
            )
            await conn.execute(
                "INSERT INTO space_categories (id, space_id, name, description,"
                " display_order, created_at, updated_at, deleted_at)"
                " VALUES ($1, $2, $3, $4, 0, now(), now(),"
                " CASE WHEN $5 THEN now() END)",
                category_id,
                space_id,
                name,
                description,
                deleted,
            )
            ids[case] = category_id

    db.run(work)
    return ids


def test_untouched_generated_categories_take_their_owners_language() -> None:
    with database_at(BEFORE) as db:
        ids = _seed(db)

        db.upgrade(AFTER)

        def named(case: str) -> tuple[str, str]:
            row = db.fetchrow(
                "SELECT name, description FROM space_categories WHERE id = $1",
                ids[case],
            )
            assert row is not None
            return row["name"], row["description"]

        assert named("zh_owner") == ("默认分类", "新建空间时自动创建的分类")
        assert named("unset_owner") == ("默认分类", "新建空间时自动创建的分类")
        assert named("en_owner") == ("General", "Created with the space")
        assert named("renamed") == ("课程作业", GENERATED[1])
        assert named("own_description") == ("General", "放所有练习题")
        assert named("deleted") == GENERATED
