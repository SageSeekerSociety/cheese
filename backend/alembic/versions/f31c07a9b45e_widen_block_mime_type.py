"""Widen blocks.mime_type — 64 chars cannot hold an Office MIME type.

`application/vnd.openxmlformats-officedocument.wordprocessingml.document` is 71
characters, so sending a .docx into a room raised
`value too long for type character varying(64)` and rolled back the whole
message: the upload had already returned 200, the person saw "消息未能保存,
请重新发送" with no cause, and every retry failed the same way. .pptx (73) and
.xlsx (65) were out too; .pdf (15) and images were fine, which is why this
survived so long.

255 is what RFC 6838 needs: a type name and a subtype name are each capped at
127 characters, plus the slash.
"""

import sqlalchemy as sa

from alembic import op

revision = "f31c07a9b45e"
down_revision = "e8b42a731c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "blocks",
        "mime_type",
        existing_type=sa.String(64),
        type_=sa.String(255),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Truncates any value the widened column now holds — which is the whole
    # point of the change, so this only goes back on a database that never
    # accepted one.
    op.alter_column(
        "blocks",
        "mime_type",
        existing_type=sa.String(255),
        type_=sa.String(64),
        existing_nullable=True,
    )
