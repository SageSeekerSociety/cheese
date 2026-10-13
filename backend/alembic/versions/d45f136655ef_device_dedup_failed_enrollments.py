"""delete the duplicate device rows failed enrollments left behind

Revision ID: d45f136655ef
Revises: ad230848b32a
Create Date: 2026-10-10

Until ``HostPool.enroll`` deleted the device a failed attempt had minted, every
failed attempt left a ``device`` row behind under the same name and owner as the
next attempt's. Those rows never connected, nothing points at them, and each one
shows up as another copy of the machine in the environment picker.

A row is deleted only when all of these hold:

- it has never been seen: ``last_seen_at`` is NULL;
- nothing refers to it: no row in any table that stores a device id
  (``device_claude_login``, ``device_health``, ``device_project``,
  ``device_topic``, ``local_directory_grant``, ``local_fs_access``,
  ``kept_room_files``, ``cloud_hosts``, ``project_machines``,
  ``warm_machines``), and its id appears in no session's execution request,
  runtime location or work lease, no topic's or task's compute choice or
  environment, no project's settings and no room cleanup's resources;
- another row with the same owner and name survives. Within each
  (owner, name) group, every row that is seen or referenced is kept; where none
  is, the newest row is kept, so a name never disappears;
- every team it is bound to (``device_team``) is also bound to a surviving
  row of its group, so no team loses the machine.

``device_team`` and ``hosted_device`` rows go with the device (ON DELETE
CASCADE): a device row is created with them, so they say nothing about use.
``device_auth_code`` keeps its rows; it has no foreign key and only records the
code that minted the device.

Before the delete, every matched device row and every row that would cascade
from it is copied into ``device_dedup_archive_20261010`` as JSON with its
source table and the time. The downgrade puts them back from there.
"""

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "d45f136655ef"
down_revision: str | Sequence[str] | None = "ad230848b32a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

# Tables with a device_id column whose rows mean the device is in use.
_USED_BY = (
    "device_claude_login",
    "device_health",
    "device_project",
    "device_topic",
    "local_directory_grant",
    "local_fs_access",
    "kept_room_files",
    "cloud_hosts",
    "project_machines",
    "warm_machines",
)

# Every table whose rows the delete would cascade into, in restore order.
_CASCADES = (
    "hosted_device",
    "device_team",
    "device_claude_login",
    "device_health",
    "device_project",
    "device_topic",
    "local_directory_grant",
)

_USED = " OR ".join(
    ["d.last_seen_at IS NOT NULL"]
    + [
        f"EXISTS (SELECT 1 FROM {t} x WHERE x.device_id = d.device_id)"
        for t in _USED_BY
    ]
    + [
        # JSON documents that name a device by id.
        "EXISTS (SELECT 1 FROM agent_sessions s WHERE strpos(concat_ws(' ',"
        " s.execution_request::text, s.runtime_location::text,"
        " s.work_lease::text), d.device_id) > 0)",
        "EXISTS (SELECT 1 FROM topics t WHERE strpos(concat_ws(' ',"
        " t.compute_config::text, t.environment::text), d.device_id) > 0)",
        "EXISTS (SELECT 1 FROM tasks k"
        " WHERE strpos(k.compute_config::text, d.device_id) > 0)",
        "EXISTS (SELECT 1 FROM projects p"
        " WHERE strpos(p.settings::text, d.device_id) > 0)",
        "EXISTS (SELECT 1 FROM room_cleanups r"
        " WHERE strpos(r.resources::text, d.device_id) > 0)",
    ]
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS device_dedup_archive_20261010 (
            id bigserial PRIMARY KEY,
            source_table varchar(64) NOT NULL,
            device_id varchar(64) NOT NULL,
            row_data jsonb NOT NULL,
            archived_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Hold every unseen row of a duplicated name while deciding, so none of
    # them connects or gains a reference between the decision and the delete.
    op.execute(
        """
        SELECT 1 FROM device d
        WHERE d.last_seen_at IS NULL
          AND (d.owner_user_id, d.name) IN (
              SELECT owner_user_id, name FROM device
              GROUP BY 1, 2 HAVING count(*) > 1)
        ORDER BY d.device_id
        FOR UPDATE
        """
    )
    op.execute(
        f"""
        CREATE TEMP TABLE device_dedup_doomed ON COMMIT DROP AS
        WITH judged AS (
            SELECT d.device_id, d.owner_user_id, d.name, ({_USED}) AS used,
                   count(*) OVER grp AS n,
                   count(*) FILTER (WHERE ({_USED})) OVER grp AS n_used,
                   row_number() OVER (
                       PARTITION BY d.owner_user_id, d.name
                       ORDER BY d.created_at DESC, d.device_id DESC) AS newest
            FROM device d
            WINDOW grp AS (PARTITION BY d.owner_user_id, d.name)
        ),
        candidate AS (
            SELECT * FROM judged
            WHERE n > 1 AND NOT used AND NOT (n_used = 0 AND newest = 1)
        )
        SELECT c.device_id FROM candidate c
        WHERE NOT EXISTS (
            SELECT 1 FROM device_team bound
            WHERE bound.device_id = c.device_id
              AND NOT EXISTS (
                  SELECT 1 FROM device_team kept
                  JOIN judged k ON k.device_id = kept.device_id
                  WHERE kept.team_id = bound.team_id
                    AND k.owner_user_id = c.owner_user_id
                    AND k.name = c.name
                    AND k.device_id NOT IN (SELECT device_id FROM candidate)))
        """
    )
    op.execute(
        """
        INSERT INTO device_dedup_archive_20261010 (source_table, device_id, row_data)
        SELECT 'device', d.device_id, to_jsonb(d) FROM device d
        WHERE d.device_id IN (SELECT device_id FROM device_dedup_doomed)
        """
    )
    for table in _CASCADES:
        op.execute(
            f"""
            INSERT INTO device_dedup_archive_20261010
                (source_table, device_id, row_data)
            SELECT '{table}', x.device_id, to_jsonb(x) FROM {table} x
            WHERE x.device_id IN (SELECT device_id FROM device_dedup_doomed)
            """
        )
    deleted = (
        op.get_bind()
        .exec_driver_sql(
            "DELETE FROM device"
            " WHERE device_id IN (SELECT device_id FROM device_dedup_doomed)"
        )
        .rowcount
    )
    log.info("device dedup: archived and deleted %s device rows", deleted)


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO device
        SELECT (jsonb_populate_record(NULL::device, a.row_data)).*
        FROM device_dedup_archive_20261010 a
        WHERE a.source_table = 'device'
        ON CONFLICT DO NOTHING
        """
    )
    for table in _CASCADES:
        op.execute(
            f"""
            INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, a.row_data)).*
            FROM device_dedup_archive_20261010 a
            WHERE a.source_table = '{table}'
            ON CONFLICT DO NOTHING
            """
        )
    op.execute("DROP TABLE device_dedup_archive_20261010")
