"""Keep the bound GitHub repository's id, which survives a rename.

Rows bound before this have no id; the backend fills it in the first time it
looks the repository up, so nothing here needs GitHub.
"""

import sqlalchemy as sa

from alembic import op

revision = "a3f70c5e9b21"
down_revision = "d3a8e51c07f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_git_installations",
        sa.Column("repository_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_git_installations", "repository_id")
