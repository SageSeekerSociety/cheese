"""A stored compute choice names only a device, never a platform preset

Revision ID: 2733a598f271
Revises: b84d0f9ac721
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2733a598f271"
down_revision: str | Sequence[str] | None = "b84d0f9ac721"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The three places a compute choice is stored, each as a path to the choice
# object inside a json column. `settings` and the other two are json, not
# jsonb, and the key-delete operator is jsonb-only.
_PLACES = (
    ("projects", "settings", "{compute_configs,default}"),
    ("topics", "compute_config", "{}"),
    ("agent_sessions", "execution_request", "{choice}"),
)


def _choice(column: str, path: str) -> str:
    return f"({column}::jsonb #> '{path}')"


def _set(column: str, path: str, value: str) -> str:
    if path == "{}":
        return f"({value})::json"
    return f"jsonb_set({column}::jsonb, '{path}', {value})::json"


def upgrade() -> None:
    """Drop the name from every stored choice that is not a named device.

    The platform's own choices — the cloud with standard or custom specs, and
    「any online device」 — used to be stored with a Chinese label as their
    name, which every member of a project then saw whatever language their
    screen was in. They are identified by their fields now and each screen
    renders the label, so the stored label goes. A device's name stays: it is
    the device's own proper noun. 「自有设备」 was the stand-in written when a
    bound device's name could not be found, so it goes too.
    """
    for table, column, path in _PLACES:
        choice = _choice(column, path)
        op.execute(
            f"""
            UPDATE {table}
            SET {column} = {_set(column, path, f"{choice} - 'name'")}
            WHERE jsonb_typeof({choice}) = 'object'
              AND {choice} ? 'name'
              AND NOT (
                {choice} ->> 'profile' = 'device'
                AND COALESCE({choice} ->> 'device_id', '') <> ''
                AND {choice} ->> 'name' <> '自有设备'
              )
            """
        )


def downgrade() -> None:
    """Put back the labels the previous code requires as a name."""
    for table, column, path in _PLACES:
        choice = _choice(column, path)
        label = f"""
            CASE
              WHEN {choice} ->> 'profile' = 'cloud'
                   AND COALESCE({choice} ->> 'cores', {choice} ->> 'memory_mb',
                                {choice} ->> 'disk_gb') IS NOT NULL
                THEN '云端 · 自定义配置'
              WHEN {choice} ->> 'profile' = 'cloud' THEN '云端 · 标准配置'
              WHEN COALESCE({choice} ->> 'device_id', '') = ''
                THEN '自有设备 · 自动选择'
              ELSE '自有设备'
            END
        """
        op.execute(
            f"""
            UPDATE {table}
            SET {column} = {_set(column, path, f"{choice} || jsonb_build_object('name', {label})")}
            WHERE jsonb_typeof({choice}) = 'object'
              AND COALESCE({choice} ->> 'name', '') = ''
            """
        )
