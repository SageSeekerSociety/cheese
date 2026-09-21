"""blocks.author_type keeps only the two values the enum still has

`AuthorType` used to have three members — `human`, `ai`, `system`. The first two
were the same thing (a participant said something; which participant is the
signature's question, not the column's), and the third was named after the
process rather than after who it is: the platform. The enum is now
`{participant, platform}` and the rows have to say the same, because
`Enum(AuthorType, native_enum=False)` binds the Python enum: a row still reading
`human` comes back as a `LookupError` the moment anything selects it, not as a
wrong avatar.

The rewrite is one-way and one-off. Nothing in the previous release writes
`human` or `ai` any more — that was the whole point of splitting this off from
the change that added `participant` — and `system` is written only by the code
this same commit renames. So there is no window in which a row of the old shape
can appear behind the migration, and no follow-up pass to schedule.

`system` → `platform` cannot be deferred past this commit either: the column
stores the member *name*, so the release that drops `system` from the enum must
carry the rows with it.

Two full passes over `blocks` with no index to help them. The predicates are not
selective enough for an index to be worth adding for two statements, and
migrations run without a statement timeout, so they finish — on a large
deployment simply slowly, which is worth knowing before the deploy rather than
during it.

There is no downgrade. Going back would mean deciding which of `human` and `ai`
each rewritten row used to be, and that distinction is exactly what this design
says the column never carried: the answer lives in `blocks.author`.

Revision ID: c1a7e05d4b83
Revises: a1286f09c001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1a7e05d4b83"
down_revision: str | Sequence[str] | None = "a1286f09c001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def rewrite_old_author_types(conn) -> None:
    """把三个旧值改写成枚举今天的两档。测试跑的就是这个函数。"""
    conn.execute(
        sa.text(
            "UPDATE blocks SET author_type = 'participant' "
            "WHERE author_type IN ('human', 'ai')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE blocks SET author_type = 'platform' WHERE author_type = 'system'"
        )
    )


def upgrade() -> None:
    rewrite_old_author_types(op.get_bind())


def downgrade() -> None:
    """不回退：见模块 docstring。"""
