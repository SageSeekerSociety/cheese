"""converge duplicate 2026-08-09 accept-card-pr-fields merge heads

Two topics independently reconciled the same (b3d5f7a9c102, f9a1c7e3b502)
fork point around the same time — 504ece6e60ea (no-op) and c7d8e9f0a1b2 (adds
accept_cards.pr_repo/pr_head_sha/pr_merged_at) — leaving two sibling heads on
main instead of one. Pure DAG plumbing, no schema change of its own.

Revision ID: c2f677c441f0
Revises: 504ece6e60ea, c7d8e9f0a1b2
Create Date: 2026-08-09 16:25:44.007436

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c2f677c441f0"
down_revision: str | Sequence[str] | None = ("504ece6e60ea", "c7d8e9f0a1b2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
