"""Notifications no longer carry a thumbs-up or thumbs-down

Revision ID: b7e3c9d14a52
Revises: b7e3c9d1f4a2
Create Date: 2026-10-07

#3022 removed the rating buttons, the endpoint behind them and the admin card
that read them; the column was empty on dev. It stayed one deploy longer
because the previous image still selected it while migrations ran. That image
is gone, so the column goes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e3c9d14a52"
down_revision: str | Sequence[str] | None = "b7e3c9d1f4a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _lock(tables: str) -> None:
    """As in b6fcc6362b79: queue for the table a few seconds at a time."""
    op.execute(f"""
        DO $$
        DECLARE
            attempts integer := 0;
            outer_timeout text := current_setting('lock_timeout');
        BEGIN
            PERFORM set_config('lock_timeout', '3s', true);
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE;
                    EXIT;
                EXCEPTION WHEN lock_not_available OR deadlock_detected THEN
                    attempts := attempts + 1;
                    IF attempts >= 100 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.2);
                END;
            END LOOP;
            PERFORM set_config('lock_timeout', outer_timeout, true);
        END
        $$
    """)


def upgrade() -> None:
    _lock("notification")
    op.drop_column("notification", "feedback")


def downgrade() -> None:
    op.add_column(
        "notification",
        sa.Column("feedback", sa.String(length=8), nullable=True),
    )
