"""Every session's place names the one executor channel

Revision ID: 76d8e010047a
Revises: 70c0b60a075a
Create Date: 2026-10-11

Sessions placed through the cloud channel recorded `channel: "cloud"` in
`agent_sessions.runtime_location`. There is one executor channel now, named
`device`, and a restart recovers only the sessions recorded under it
(`CentralChannel.placed`), so those rows are renamed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "76d8e010047a"
down_revision: str | Sequence[str] | None = "70c0b60a075a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE agent_sessions
        SET runtime_location = jsonb_set(
            runtime_location::jsonb, '{channel}', '"device"'
        )::json
        WHERE runtime_location->>'channel' = 'cloud'
        """
    )


def downgrade() -> None:
    """Nothing to restore: which rows were "cloud" is not kept, and the release
    before this one recovers a session recorded as "device" on its device
    channel, which reaches the same machine over the same link."""
