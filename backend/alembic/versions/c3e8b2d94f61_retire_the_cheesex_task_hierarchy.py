"""retire the cheesex 题目 hierarchy

Second half of the 赛题 merge (#370). The protocol moved to the 知是 levels in
a1f4c73b5e28; these four tables are what it moved OFF, and nothing reads them any
more.

**This migration refuses to run if any of them holds a row.** Dropping a table is
not reversible by a downgrade — the rows are gone — and this checkout cannot see
what a deployment's database contains. The market had no UI to create a template,
so these are expected to be empty everywhere; "expected" is not "verified", and
the difference between the two is someone's data. A deployment that IS carrying
rows gets a loud, actionable failure instead of a silent loss.

If it fires: those rows describe 题目 and 应征 that the 知是 side now expresses as
赛题 (`task`) and 领取 (`task_membership`). Move them by hand, or drop them
deliberately, then re-run.

`compute_grants.source_task_id` changes type with them: it pointed at
`tasks.id` (uuid) and now names the 知是 赛题 (int). The guard above is what
makes that lossless — with `tasks` empty, no grant can be pointing at one.

Revision ID: c3e8b2d94f61
Revises: a1f4c73b5e28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e8b2d94f61"
down_revision: str | Sequence[str] | None = "a1f4c73b5e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOOMED = ("task_applications", "project_task_links", "tasks", "task_templates")


def _refuse_if_populated() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    carrying = {}
    for table in _DOOMED:
        if table not in existing:
            continue
        count = bind.execute(sa.text(f'SELECT count(*) FROM "{table}"')).scalar_one()
        if count:
            carrying[table] = count
    if carrying:
        raise RuntimeError(
            "refusing to drop the cheesex 题目 tables — they still hold rows: "
            + ", ".join(f"{t}={n}" for t, n in sorted(carrying.items()))
            + ". Dropping them destroys those rows and no downgrade brings them "
            "back. Move them onto the 知是 赛题 hierarchy (task / task_membership) "
            "or delete them deliberately, then run this migration again."
        )


def upgrade() -> None:
    _refuse_if_populated()
    # Order matters, and PostgreSQL says so out loud: retyping a column while it
    # still carries a foreign key fails with "foreign key constraint
    # compute_grants_source_task_id_fkey cannot be implemented" — a uuid key
    # cannot point at a bigint. Drop the constraint first, then retype, then drop
    # the tables it referred to.
    op.execute(
        "ALTER TABLE compute_grants "
        "DROP CONSTRAINT IF EXISTS compute_grants_source_task_id_fkey"
    )
    op.alter_column(
        "compute_grants",
        "source_task_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        type_=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="NULL",
    )
    for table in _DOOMED:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def downgrade() -> None:
    raise RuntimeError(
        "irreversible: the cheesex 题目 tables were dropped after being verified "
        "empty. Recreating them empty would restore the schema and none of the "
        "meaning; if this is needed, revive them from a1f4c73b5e28's parent."
    )
