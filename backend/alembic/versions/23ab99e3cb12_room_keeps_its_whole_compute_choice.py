"""A room that has run keeps the whole compute choice, not just its pool

Revision ID: 23ab99e3cb12
Revises: 872d78113ce9
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "23ab99e3cb12"
down_revision: str | Sequence[str] | None = "872d78113ce9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Give every room that recorded only a pool name its whole choice.

    A room's first run used to write only the pool (`topics.compute_profile`),
    so every later agent session in the room was handed that pool's standard
    choice instead of the spec or the machine the first session got. The code
    now writes and reads `topics.compute_config` alone; this fills it for the
    rooms that already ran. The nearest record of what such a room started with
    is its earliest session's choice — as that session holds it now, so one
    moved to another machine since carries that one. A room with no session
    choice keeps the standard choice of its pool, which is what it resolved to
    until now.

    `compute_profile` stays in place: the previous backend image still reads it
    while this runs. It is dropped once no deployed release reads it.
    """
    op.execute(
        """
        UPDATE topics t
        SET compute_config = COALESCE(
            (
                SELECT s.execution_request -> 'choice'
                FROM agent_sessions s
                WHERE s.topic_id = t.id
                  AND s.execution_request -> 'choice' IS NOT NULL
                ORDER BY s.created_at, s.id
                LIMIT 1
            ),
            CASE t.compute_profile
                WHEN 'cloud' THEN json_build_object(
                    'name', '云端 · 标准配置', 'profile', 'cloud',
                    'device_id', NULL, 'cores', NULL,
                    'memory_mb', NULL, 'disk_gb', NULL)
                ELSE json_build_object(
                    'name', '自有设备 · 自动选择', 'profile', 'device',
                    'device_id', NULL, 'cores', NULL,
                    'memory_mb', NULL, 'disk_gb', NULL)
            END
        )
        WHERE t.compute_config IS NULL
          AND t.compute_profile IN ('cloud', 'device')
        """
    )


def downgrade() -> None:
    """Nothing to undo: the previous code reads `compute_config` before
    `compute_profile` and understands every value written here."""
    pass
