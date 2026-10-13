"""Subscription usage landed without its conversation finds it

Revision ID: 7ca6c79ea15b
Revises: 52fee3dd7773
Create Date: 2026-10-06

From 06:11 to 13:00 UTC on 2026-10-06 the dev deployment ingested 435
subscription usage rows with no conversation and no turn: the ingest looked the
proxy's ``topic_id`` up in ``topics``, which holds rooms only, so every task's
spend was kept as project spend (fixed in 3ea75c1a3, #2907). The proxy log still
holds the line each row was ingested from; each row matched exactly one line
logged before it by project, model and its token counts, and that line names the
conversation. The turn is the one the ingest itself assigns: the latest turn of
that conversation whose first block precedes the line, within six hours.

The pairs are in ``7ca6c79ea15b_usage_attribution.json``. Only a row that still
has no conversation, and whose conversation still exists in its project, is
filled in, so on any other deployment, where none of these ids exist, nothing
changes.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "7ca6c79ea15b"
down_revision: str | Sequence[str] | None = "52fee3dd7773"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pairs = json.loads(
        Path(__file__).with_name("7ca6c79ea15b_usage_attribution.json").read_text()
    )
    op.get_bind().execute(
        sa.text("""
            UPDATE resource_usage AS u
               SET conversation_id = CAST(:conversation_id AS uuid),
                   turn_id = CAST(:turn_id AS uuid)
             WHERE u.id = CAST(:id AS uuid)
               AND u.conversation_id IS NULL
               AND EXISTS (
                   SELECT 1 FROM conversations c
                    WHERE c.id = CAST(:conversation_id AS uuid)
                      AND c.project_id = u.project_id
               )
        """),
        pairs,
    )


def downgrade() -> None:
    """The attribution is correct under the previous revision's schema too, and
    putting the rows back to no conversation would only restore the bug."""
    pass
