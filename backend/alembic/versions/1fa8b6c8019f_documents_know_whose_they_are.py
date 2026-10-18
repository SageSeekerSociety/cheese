"""documents.created_by, and roles that share documents

Revision ID: 1fa8b6c8019f
Revises: 569e06806d03
Create Date: 2026-10-18

A document's ``author`` is whoever made its latest version, so the rights a
team's document gave its author moved to whoever last edited it, and a
document 芝士 wrote was nobody's. ``created_by`` is the person it is theirs:
who made it, or whose message had 芝士 write it. An existing document gets the
person who made its first version, or its author while it has none; who asked
芝士 for one is not recorded anywhere, so those stay empty.

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
down_revision: str | Sequence[str] | None = "569e06806d03"
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
    first = _PERSON.format(h="first.actor")
    op.execute(
        f"""
        UPDATE documents AS d SET created_by = first.actor
        FROM (
            SELECT DISTINCT ON (document_id) document_id, actor
            FROM document_versions
            ORDER BY document_id, version
        ) AS first
        WHERE first.document_id = d.id AND {first}
        """
    )
    unwritten = _PERSON.format(h="d.author")
    op.execute(
        f"""
        UPDATE documents AS d SET created_by = d.author
        WHERE d.version = 0 AND d.created_by IS NULL AND {unwritten}
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
    op.drop_column("documents", "created_by")
