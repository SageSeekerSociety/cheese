"""A sandbox's home is kept on any machine, a host's or a person's computer

Revision ID: d7a3e5b1c829
Revises: 8b2f4d6a1c37
Create Date: 2026-10-12

`sandbox_homes` replaces `cloud_host_homes`: a home is on one of the
platform's hosts (`host_id`) or on a person's computer (`device_id`), and
carries the spec its sandbox was made for, whose idle time the lifecycle holds
it to. Every home there is now is on a host, so each is copied with the spec
its host gives it: a whole VM's is `vm`, any other `standard`.

`cloud_host_homes` stays until the release after this one, which no longer
writes it, copies again what the outgoing release wrote and drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d7a3e5b1c829"
down_revision: str | Sequence[str] | None = "8b2f4d6a1c37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A new table's foreign keys lock what they point at.
    with_lock_retries("cloud_hosts, device, projects, conversations")
    op.create_table(
        "sandbox_homes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("host_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("spec", sa.String(length=80), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("room_resource_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(host_id IS NULL) <> (device_id IS NULL)",
            name="ck_sandbox_homes_one_machine",
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["cloud_hosts.id"],
            ondelete="CASCADE",
            name="fk_sandbox_homes_host_id_cloud_hosts",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.device_id"],
            ondelete="CASCADE",
            name="fk_sandbox_homes_device_id_device",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_sandbox_homes_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["conversations.id"],
            ondelete="CASCADE",
            name="fk_sandbox_homes_topic_id_conversations",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sandbox_homes_host_id", "sandbox_homes", ["host_id"])
    op.create_index("ix_sandbox_homes_device_id", "sandbox_homes", ["device_id"])
    op.create_index("ix_sandbox_homes_project_id", "sandbox_homes", ["project_id"])
    op.create_index("ix_sandbox_homes_topic_id", "sandbox_homes", ["topic_id"])
    op.create_index(
        "uq_sandbox_homes_current_session",
        "sandbox_homes",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.execute(COPY)


#: The homes `cloud_host_homes` holds that `sandbox_homes` does not; run again
#: by the release that drops the old table.
COPY = """
    INSERT INTO sandbox_homes
      (id, created_at, updated_at, host_id, spec, project_id, topic_id,
       room_resource_id, resource_id, session_id, waiting_since, active_at,
       stopped_at)
    SELECT o.id, o.created_at, o.updated_at, o.host_id,
           CASE WHEN h.whole_machine THEN 'vm' ELSE 'standard' END,
           o.project_id, o.topic_id, o.room_resource_id, o.resource_id,
           o.session_id, o.waiting_since, o.active_at, o.stopped_at
    FROM cloud_host_homes o
    JOIN cloud_hosts h ON h.id = o.host_id
    WHERE NOT EXISTS (SELECT 1 FROM sandbox_homes n WHERE n.id = o.id)
      AND (o.session_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM sandbox_homes n WHERE n.session_id = o.session_id
      ))
"""


def downgrade() -> None:
    # The release before this one keeps its homes in `cloud_host_homes`, which
    # this one left in place; homes made since on people's computers are not
    # its to know.
    op.drop_table("sandbox_homes")
