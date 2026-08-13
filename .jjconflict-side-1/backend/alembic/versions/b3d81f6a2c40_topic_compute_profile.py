"""topics.compute_profile: per-topic compute selection (execution-architecture v4)

A topic's turns run on the compute pool it selected (会话级选择). NULL = inherit the
project's sticky default (project.settings.compute_profile). Switchable only before
the first turn (session_id IS NULL); frozen after. Additive only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d81f6a2c40"
down_revision: str | Sequence[str] | None = "e7c2a4f19d33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column("compute_profile", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("topics", "compute_profile")
