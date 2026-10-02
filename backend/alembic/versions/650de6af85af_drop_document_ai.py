"""The document AI side panel is gone; its requests, attempts and proposals go.

People now ask the room's agent to change the document directly (a selection
rewrite, a comment that mentions it, a chat message), so nothing reads these
tables any more.

Revision ID: 650de6af85af
Revises: 1dd568ffdeef
"""

import importlib.util
from pathlib import Path

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
    # The tables come back as the migration that made them built them, empty:
    # the rows went with the upgrade.
    created = Path(__file__).with_name("a27d91f0b63e_document_ai_work.py")
    spec = importlib.util.spec_from_file_location("document_ai_work", created)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.upgrade()
