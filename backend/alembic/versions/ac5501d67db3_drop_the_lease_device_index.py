"""Drop the index of the device a session's lease is on

Revision ID: ac5501d67db3
Revises: 4c21cc2f20a4

Which session holds a sandbox on a device was asked of
``agent_sessions.work_lease`` through ``ix_agent_sessions_lease_device``. It
is asked of the environment tables now (``environments.device_id``), and
nothing of this release reads the lease, so the index only costs the previous
release's writes while it still serves.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ac5501d67db3"
down_revision: str | Sequence[str] | None = "4c21cc2f20a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_agent_sessions_lease_device",
            table_name="agent_sessions",
            postgresql_concurrently=True,
            if_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_agent_sessions_lease_device"
            " ON agent_sessions ((work_lease ->> 'device_id'))"
        )
