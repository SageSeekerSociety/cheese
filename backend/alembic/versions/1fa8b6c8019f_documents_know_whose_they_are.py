"""documents know their authors, and roles that share documents

Revision ID: 1fa8b6c8019f
Revises: 32b875c31a9a
Create Date: 2026-10-18

A document's ``author`` is whoever made its latest version, so the rights a
team's document gave its author moved to whoever last edited it, and a
document 芝士 wrote was nobody's. Two columns fix its authors when it is made:
``created_by``, the person it is theirs — who made it, or whose message had
芝士 write it — and ``drafted_by``, the AI teammate that wrote it. An existing
document's first version (its author while it has none) says which of the two
made it; whom 芝士 wrote one for was never recorded, so those have no person.

Sharing a project's document becomes a permission of its own,
``share_documents``, on admins and members by default. A project that changed
a role keeps that role's whole list, so admins and members there get it too:
an admin shared before, through the project's settings.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "1fa8b6c8019f"
down_revision: str | Sequence[str] | None = "32b875c31a9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``identity.handles.names_a_person``, in SQL: not a sentinel, not an agent.
_PERSON = """
    lower({h}) NOT IN ('anonymous', 'system', 'cheese')
    AND {h} NOT LIKE 'cheese-%'
"""


def upgrade() -> None:
    with_lock_retries("documents")
    op.add_column("documents", sa.Column("created_by", sa.String(128), nullable=True))
    op.add_column("documents", sa.Column("drafted_by", sa.String(128), nullable=True))
    person = _PERSON.format(h="maker.handle")
    op.execute(
        f"""
        WITH maker AS (
            SELECT d.id,
                   COALESCE(
                       (SELECT v.actor FROM document_versions v
                        WHERE v.document_id = d.id
                        ORDER BY v.version LIMIT 1),
                       d.author
                   ) AS handle
            FROM documents d
        )
        UPDATE documents AS d SET
            created_by = CASE WHEN {person} THEN maker.handle END,
            drafted_by = CASE
                WHEN lower(maker.handle) = 'cheese' OR maker.handle LIKE 'cheese-%'
                THEN maker.handle
            END
        FROM maker
        WHERE maker.id = d.id
        """
    )
    op.execute(
        """
        UPDATE project_role_grants
        SET permissions = permissions || '["share_documents"]'::jsonb
        WHERE role IN ('admin', 'member')
          AND NOT permissions ? 'share_documents'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE project_role_grants
        SET permissions = permissions - 'share_documents'
        """
    )
    op.drop_column("documents", "drafted_by")
    op.drop_column("documents", "created_by")
