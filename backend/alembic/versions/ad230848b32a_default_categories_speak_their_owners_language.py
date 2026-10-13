"""a space's untouched auto-created category takes its owner's language

Revision ID: ad230848b32a
Revises: 26ba2f15735b
Create Date: 2026-10-10

A new space's first category was named ``General`` / ``Auto generated default
category`` whatever language its creator used, so Chinese-speaking owners saw
English in the sidebar and the publish form. New spaces now get the name in the
creator's UI language (``SpaceService.create_space``). This gives the spaces
created before that change the same name.

Only a row still carrying both the generated name and the generated description
is renamed: that pair is text the platform wrote and nobody has changed since. A
category its owner renamed, or whose description they rewrote, keeps what they
wrote. The language is the space owner's (``user.language``; unset reads as
Chinese, as in ``create_space``).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ad230848b32a"
down_revision: str | Sequence[str] | None = "26ba2f15735b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied from app.domain.space.services.DEFAULT_CATEGORY at the time of writing
# (migrations do not import app.*).
_ZH = ("默认分类", "新建空间时自动创建的分类")
_EN = ("General", "Created with the space")


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE space_categories AS c
        SET name = CASE WHEN o.language = 'en' THEN '{_EN[0]}' ELSE '{_ZH[0]}' END,
            description = CASE WHEN o.language = 'en' THEN '{_EN[1]}'
                               ELSE '{_ZH[1]}' END,
            updated_at = now()
        FROM space AS s
        LEFT JOIN LATERAL (
            SELECT u.language
            FROM space_admin_relation AS a
            JOIN "user" AS u ON u.id = a.user_id
            WHERE a.space_id = s.id AND a.role = 0 AND a.deleted_at IS NULL
            ORDER BY a.id
            LIMIT 1
        ) AS o ON true
        WHERE c.space_id = s.id
          AND c.deleted_at IS NULL
          AND c.name = 'General'
          AND c.description = 'Auto generated default category'
        """
    )


def downgrade() -> None:
    """Leaves the localized names in place: a space created after the upgrade
    carries the same names, so which rows this migration renamed cannot be told
    apart from those, and renaming them back would put English on spaces that
    never had it."""
