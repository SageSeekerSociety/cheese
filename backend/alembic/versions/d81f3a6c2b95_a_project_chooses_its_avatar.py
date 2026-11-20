"""A project chooses its avatar

Revision ID: d81f3a6c2b95
Revises: cf5d978e2f7d
Create Date: 2026-11-18

A project's tile showed the first character of its name, so projects whose
names start alike looked the same. Four nullable columns let a project pick
what its tile shows (an icon, an emoji, a few letters or an uploaded picture)
and its colour. Every existing project keeps NULL in all four: the default tile,
drawn from its name and id. The previous release maps none of them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d81f3a6c2b95"
down_revision: str | Sequence[str] | None = "cf5d978e2f7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_projects_avatar_image_id_avatar"
_INDEX = "ix_projects_avatar_image_id"
_CHECKS = {
    "ck_projects_avatar": (
        "(avatar_kind IS NULL AND avatar_glyph IS NULL"
        " AND avatar_image_id IS NULL)"
        " OR (avatar_kind IN ('icon', 'emoji', 'text')"
        " AND avatar_glyph IS NOT NULL AND avatar_image_id IS NULL)"
        " OR (avatar_kind = 'image' AND avatar_image_id IS NOT NULL"
        " AND avatar_glyph IS NULL)"
    ),
    "ck_projects_avatar_hue": "avatar_hue IS NULL OR avatar_hue BETWEEN 0 AND 359",
}


def upgrade() -> None:
    # The new foreign key locks `avatar` as well as `projects`.
    with_lock_retries("projects, avatar")
    op.add_column("projects", sa.Column("avatar_kind", sa.String(8), nullable=True))
    op.add_column("projects", sa.Column("avatar_glyph", sa.String(64), nullable=True))
    op.add_column("projects", sa.Column("avatar_hue", sa.SmallInteger(), nullable=True))
    op.add_column("projects", sa.Column("avatar_image_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        _FK,
        "projects",
        "avatar",
        ["avatar_image_id"],
        ["id"],
        postgresql_not_valid=True,
    )
    for name, condition in _CHECKS.items():
        op.create_check_constraint(
            name, "projects", condition, postgresql_not_valid=True
        )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE projects VALIDATE CONSTRAINT {_FK}")
        for name in _CHECKS:
            op.execute(f"ALTER TABLE projects VALIDATE CONSTRAINT {name}")
        op.create_index(
            _INDEX,
            "projects",
            ["avatar_image_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="projects",
            postgresql_concurrently=True,
            if_exists=True,
        )
    for name in _CHECKS:
        op.drop_constraint(name, "projects", type_="check")
    op.drop_constraint(_FK, "projects", type_="foreignkey")
    op.drop_column("projects", "avatar_image_id")
    op.drop_column("projects", "avatar_hue")
    op.drop_column("projects", "avatar_glyph")
    op.drop_column("projects", "avatar_kind")
