"""A project chooses who its commits are credited to

Revision ID: eef0ae0a8056
Revises: 1fa8b6c8019f
Create Date: 2026-10-18

``forge_requester_coauthor`` (whether the requester was added as a co-author to
an App-authored commit) gives way to ``commit_credit``: ``owner`` — the task
owner authors the commit, the default — or ``agent`` — the AI teammate does and
no person is named. A project that had turned the co-author off asked for the
second; every other one reads as the default.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "eef0ae0a8056"
down_revision: str | Sequence[str] | None = "1fa8b6c8019f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE projects
        SET settings = (settings::jsonb || '{"commit_credit": "agent"}'::jsonb)::json
        WHERE settings::jsonb ->> 'forge_requester_coauthor' = 'false'
        """
    )
    op.execute(
        """
        UPDATE projects
        SET settings = (settings::jsonb - 'forge_requester_coauthor')::json
        WHERE settings::jsonb ? 'forge_requester_coauthor'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE projects
        SET settings = (
            (settings::jsonb - 'commit_credit')
            || '{"forge_requester_coauthor": false}'::jsonb
        )::json
        WHERE settings::jsonb ->> 'commit_credit' = 'agent'
        """
    )
    op.execute(
        """
        UPDATE projects
        SET settings = (settings::jsonb - 'commit_credit')::json
        WHERE settings::jsonb ? 'commit_credit'
        """
    )
