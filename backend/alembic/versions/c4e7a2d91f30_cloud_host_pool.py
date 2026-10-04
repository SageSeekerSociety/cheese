"""Cloud hosts become a platform-owned pool shared by every project.

A host belongs to no project, room or session: ``cloud_hosts`` holds the
machines and ``cloud_host_homes`` which sessions' homes are on each.

Every machine still held in ``project_machines`` is adopted as a *draining*
host under its own id: it keeps the sessions already on it, takes no new one
(it was enrolled before session sandboxes existed), and is released by the pool
once no home is left on it. Homes are written for what is on those machines:
each session's current lease and each lease a session left there without
pushing, each session allocation not leased yet, and the room directory of a
pre-session room machine. Devices of adopted hosts leave the team and project
pools, where they were listed as the team's machines.

``project_machines`` itself is left in place, unread and unwritten: the device
connection owner outlives app releases and reads it (``machine.owner_reads``
before this release) until it is released itself. A later migration drops it.

The team cloud machine limit goes, and so does the cloud spec a compute choice
could carry: every sandbox gets the same share of a host.
"""

import sqlalchemy as sa

from alembic import op

revision = "c4e7a2d91f30"
down_revision = "c455bd47d0ac"
branch_labels = None
depends_on = None

_SPEC = "ARRAY['cores', 'memory_mb', 'disk_gb']"


def upgrade() -> None:
    op.create_table(
        "cloud_hosts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("machine_id", sa.BigInteger(), nullable=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("offering_id", sa.BigInteger(), nullable=False),
        sa.Column("hostname", sa.String(64), nullable=False),
        sa.Column("login_user", sa.String(32), nullable=False),
        sa.Column("cores", sa.BigInteger(), nullable=False),
        sa.Column("memory_mb", sa.BigInteger(), nullable=False),
        sa.Column("disk_gb", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("ai_mode", sa.String(16), nullable=False, server_default="none"),
        sa.Column("ai_status", sa.String(16), nullable=False),
        sa.Column(
            "warm_claim_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("device_id", sa.String(64), nullable=True, unique=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enroll_error", sa.Text(), nullable=True),
        sa.Column("enroll_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bootstrap_key", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draining", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("idle_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cloud_hosts_machine_id", "cloud_hosts", ["machine_id"])
    op.create_table(
        "cloud_host_homes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "host_id",
            sa.Uuid(),
            sa.ForeignKey("cloud_hosts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "topic_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("room_resource_id", sa.String(36), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cloud_host_homes_host_id", "cloud_host_homes", ["host_id"])
    op.create_index("ix_cloud_host_homes_topic_id", "cloud_host_homes", ["topic_id"])
    op.create_index(
        "uq_cloud_host_homes_current_session",
        "cloud_host_homes",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL AND session_id IS NOT NULL"),
    )

    # Every machine still held becomes a draining host, under the same id so a
    # warm claim in flight keeps its key. One row per device: two rows naming
    # one device never both described a live machine.
    op.execute(
        """
        INSERT INTO cloud_hosts (
            id, created_at, updated_at, machine_id, customer_id, account_id,
            offering_id, hostname, login_user, cores, memory_mb, disk_gb,
            status, ip, ai_mode, ai_status, warm_claim_pending, device_id,
            enrolled_at, enroll_error, enroll_attempts, bootstrap_key,
            last_seen_at, draining
        )
        SELECT DISTINCT ON (coalesce(device_id, id::text))
            id, created_at, now(), machine_id, customer_id, account_id,
            offering_id, hostname, login_user, cores, memory_mb, disk_gb,
            status, ip, ai_mode, ai_status, warm_claim_pending, device_id,
            enrolled_at, enroll_error, enroll_attempts, bootstrap_key,
            last_seen_at, true
        FROM project_machines
        WHERE released_at IS NULL
        ORDER BY coalesce(device_id, id::text), superseded_at NULLS FIRST, created_at
        """
    )
    # Each session's current lease on an adopted host.
    op.execute(
        """
        INSERT INTO cloud_host_homes (
            id, created_at, updated_at, host_id, project_id, topic_id,
            room_resource_id, resource_id, session_id
        )
        SELECT gen_random_uuid(), now(), now(), h.id, t.project_id, t.id,
            coalesce(
                s.work_lease::jsonb ->> 'room_resource_id',
                coalesce(t.resource_id, t.id)::text
            ),
            coalesce(
                s.work_lease::jsonb ->> 'resource_id',
                s.execution_request::jsonb ->> 'generation',
                coalesce(t.resource_id, t.id)::text
            ),
            s.id
        FROM agent_sessions s
        JOIN topics t ON t.id = s.topic_id
        JOIN cloud_hosts h ON h.device_id = s.work_lease::jsonb ->> 'device_id'
        WHERE s.work_lease IS NOT NULL
        """
    )
    # Each lease a session left on one without pushing: its work is only there.
    op.execute(
        """
        INSERT INTO cloud_host_homes (
            id, created_at, updated_at, host_id, project_id, topic_id,
            room_resource_id, resource_id, session_id, left_at
        )
        SELECT gen_random_uuid(), now(), now(), h.id, t.project_id, t.id,
            coalesce(
                lease ->> 'room_resource_id', coalesce(t.resource_id, t.id)::text
            ),
            lease ->> 'resource_id',
            s.id, now()
        FROM agent_sessions s
        JOIN topics t ON t.id = s.topic_id
        CROSS JOIN LATERAL jsonb_array_elements(
            coalesce(s.execution_request::jsonb -> 'retained_leases', '[]'::jsonb)
        ) AS lease
        JOIN cloud_hosts h ON h.device_id = lease ->> 'device_id'
        WHERE lease ->> 'resource_id' IS NOT NULL
        """
    )
    # A session's allocation it holds no lease on yet: it waits for that host.
    op.execute(
        """
        INSERT INTO cloud_host_homes (
            id, created_at, updated_at, host_id, project_id, topic_id,
            room_resource_id, resource_id, session_id
        )
        SELECT gen_random_uuid(), now(), now(), h.id, t.project_id, t.id,
            coalesce(t.resource_id, t.id)::text,
            coalesce(
                s.execution_request::jsonb ->> 'generation',
                coalesce(t.resource_id, t.id)::text
            ),
            s.id
        FROM project_machines m
        JOIN cloud_hosts h ON h.id = m.id
        JOIN agent_sessions s ON s.id = m.session_id
        JOIN topics t ON t.id = s.topic_id
        WHERE m.superseded_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM cloud_host_homes o
            WHERE o.session_id = s.id AND o.left_at IS NULL
          )
        """
    )
    # A room's machine from before session leases: the room's directory.
    op.execute(
        """
        INSERT INTO cloud_host_homes (
            id, created_at, updated_at, host_id, project_id, topic_id,
            room_resource_id, resource_id
        )
        SELECT gen_random_uuid(), now(), now(), h.id, t.project_id, t.id,
            coalesce(t.resource_id, t.id)::text,
            coalesce(t.resource_id, t.id)::text
        FROM project_machines m
        JOIN cloud_hosts h ON h.id = m.id
        JOIN topics t ON t.id = m.topic_id
        WHERE m.session_id IS NULL AND m.superseded_at IS NULL
        """
    )
    # Hosts are nobody's team's or project's machines.
    op.execute(
        """
        DELETE FROM device_team WHERE device_id IN (
            SELECT device_id FROM cloud_hosts WHERE device_id IS NOT NULL
        )
        """
    )
    op.execute(
        """
        DELETE FROM device_project WHERE device_id IN (
            SELECT device_id FROM cloud_hosts WHERE device_id IS NOT NULL
        )
        """
    )

    # A warm machine is claimed into a host now.
    op.drop_constraint(
        "warm_machines_claimed_machine_id_fkey", "warm_machines", type_="foreignkey"
    )
    op.alter_column(
        "warm_machines", "claimed_machine_id", new_column_name="claimed_host_id"
    )
    op.execute(
        "ALTER TABLE warm_machines RENAME CONSTRAINT "
        "warm_machines_claimed_machine_id_key TO warm_machines_claimed_host_id_key"
    )
    op.execute(
        """
        UPDATE warm_machines SET claimed_host_id = NULL
        WHERE claimed_host_id IS NOT NULL
          AND claimed_host_id NOT IN (SELECT id FROM cloud_hosts)
        """
    )
    op.create_foreign_key(
        "warm_machines_claimed_host_id_fkey",
        "warm_machines",
        "cloud_hosts",
        ["claimed_host_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_table("team_machine_limit")
    op.drop_table("machine_limit")

    # A cloud choice has no spec any more.
    op.execute(
        f"""
        UPDATE topics
        SET compute_config = (compute_config::jsonb - {_SPEC})::json
        WHERE compute_config IS NOT NULL
          AND compute_config::jsonb ?| {_SPEC}
        """
    )
    op.execute(
        f"""
        UPDATE projects
        SET settings = jsonb_set(
            settings::jsonb,
            '{{compute_configs,default}}',
            (settings::jsonb -> 'compute_configs' -> 'default') - {_SPEC}
        )::json
        WHERE settings::jsonb -> 'compute_configs' -> 'default' ?| {_SPEC}
        """
    )
    op.execute(
        f"""
        UPDATE agent_sessions
        SET execution_request = jsonb_set(
            execution_request::jsonb,
            '{{choice}}',
            (execution_request::jsonb -> 'choice') - {_SPEC}
        )::json
        WHERE execution_request::jsonb -> 'choice' ?| {_SPEC}
        """
    )


def downgrade() -> None:
    # The pool cannot be handed back to projects: a host carries sessions of
    # many, and the per-project MicroCloud accounts its machines would need were
    # never opened for the hosts the pool created. Stored cloud specs stay gone.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM cloud_hosts)")):
        raise RuntimeError("Release every cloud host before downgrading")
    op.create_table(
        "machine_limit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_machine_limit_singleton"),
        sa.CheckConstraint("value > 0", name="ck_machine_limit_positive"),
    )
    op.create_table(
        "team_machine_limit",
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("team.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.CheckConstraint("value > 0", name="ck_team_machine_limit_positive"),
    )
    op.drop_constraint(
        "warm_machines_claimed_host_id_fkey", "warm_machines", type_="foreignkey"
    )
    op.alter_column(
        "warm_machines", "claimed_host_id", new_column_name="claimed_machine_id"
    )
    op.execute(
        "ALTER TABLE warm_machines RENAME CONSTRAINT "
        "warm_machines_claimed_host_id_key TO warm_machines_claimed_machine_id_key"
    )
    op.create_foreign_key(
        "warm_machines_claimed_machine_id_fkey",
        "warm_machines",
        "project_machines",
        ["claimed_machine_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_table("cloud_host_homes")
    op.drop_table("cloud_hosts")
