"""The document AI side panel is gone; its requests, attempts and proposals go.

People now ask the room's agent to change the document directly (a selection
rewrite, a comment that mentions it, a chat message), so nothing reads these
tables any more.

Revision ID: 650de6af85af
Revises: 1dd568ffdeef
"""

from alembic import op

revision = "650de6af85af"
down_revision = "1dd568ffdeef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("doc_ai_proposals")
    op.drop_table("doc_ai_attempts")
    op.drop_table("doc_ai_requests")
    op.execute("DROP FUNCTION IF EXISTS freeze_doc_ai_data()")


def downgrade() -> None:
    raise RuntimeError(
        "irreversible: the document AI requests, attempts and proposals were "
        "dropped with their rows"
    )
