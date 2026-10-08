"""Every library record names where its bytes are

Revision ID: c47997681006
Revises: e5b1c7d29f04
Create Date: 2026-10-08

``0c800ff1db2f`` added ``library_files.blob_key`` and left it empty on the
rows written before it, because the release it replaced still wrote the old
way during that deploy. That release is gone: every write since sets the
key. The empty ones get the place their bytes were written to, the same
rule the code derived until now (``records.blob_key`` as of #3135):

- the current version of a name is at ``.library/<project>/<name>``;
- a replaced version is at ``.library-history/<project>/<record>/<leaf>``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c47997681006"
down_revision: str | Sequence[str] | None = "e5b1c7d29f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE library_files SET blob_key = CASE
            WHEN superseded_at IS NULL
                THEN '.library/' || project_id || '/' || name
            ELSE '.library-history/' || project_id || '/' || id || '/'
                 || regexp_replace(name, '^.*/', '')
        END
        WHERE blob_key IS NULL
    """)


def downgrade() -> None:
    """填上的键留着：它们就是旧代码按目录推出来的那一把，退回去的代码读到的位置不变。"""
