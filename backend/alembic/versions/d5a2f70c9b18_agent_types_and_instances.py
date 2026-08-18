"""agent types and instances: an agent's memory follows the agent, not the room

Three moves, one shape:

1. ``custom_roles`` becomes ``agent_types`` and grows the half of "an agent"
   that a persona never covered — how it runs (``model`` / ``effort`` /
   ``harness``) and what it can reach (``skills`` / ``mcp_servers``). A rename
   rather than a new table: every row in it already IS an agent type, and
   copying them across would leave two places to look.
2. ``agent_instances`` is new — one agent, inside one project, owning what it
   learned there. Memory used to be keyed by a handle derived from the topic id,
   which made the real unit of memory the room; this row is the unit that
   replaces it.
3. ``projects.expert_role`` (a name) becomes ``projects.default_agent_instance_id``
   (a pointer), and ``topics.agent_instance_id`` says which agent works in a
   room. NULL on both sides means "the implicit 芝士", so a project that never
   configured anything needs no row to resolve.

**The persona is carried over, not dropped.** Every project whose
``expert_role`` was set gets a real default instance under the handle ``cheese``
wearing that type. That handle is deliberately the one the platform-wide 芝士
already used, so the instance inherits the pool the project's 芝士 had been
filling instead of waking up with no memory.

Nothing else is backfilled, on purpose: the per-room pools written under
``cheese-<topic hex>`` stay exactly where they are and stay readable (the
services read them as a tail). Merging them into one agent's pool has to wait
for memory injection to stop being a flat 50-fact dump, or the merge lands on
the day 芝士 can only see a third of what it knows.

``expert_roles`` goes too. It is create-only — the full-schema migration built
it and no service ever read or wrote a row — so it is dead weight rather than
data, but it is dropped behind the same populated-check as any other table,
because "expected to be empty" and "verified empty" differ by somebody's rows.

Revision ID: d5a2f70c9b18
Revises: c3e8b2d94f61
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5a2f70c9b18"
down_revision: str | Sequence[str] | None = "c3e8b2d94f61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHEESE_HANDLE = "cheese"
CHEESE_NAME = "芝士"


def _refuse_if_populated(table: str) -> None:
    bind = op.get_bind()
    if table not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(sa.text(f'SELECT count(*) FROM "{table}"')).scalar_one()
    if count:
        raise RuntimeError(
            f"refusing to drop {table!r} — it still holds {count} row(s), and no "
            "downgrade brings them back. Nothing in the codebase has ever read or "
            "written this table; if your deployment did, move those rows onto "
            "agent_types by hand or delete them deliberately, then re-run."
        )


def upgrade() -> None:
    # --- 1. custom_roles → agent_types, plus the "how it runs" half ----------
    op.rename_table("custom_roles", "agent_types")
    op.execute("ALTER INDEX ix_custom_roles_space_id RENAME TO ix_agent_types_space_id")
    op.execute(
        "ALTER TABLE agent_types RENAME CONSTRAINT custom_roles_name_key "
        "TO agent_types_name_key"
    )
    op.add_column(
        "agent_types",
        sa.Column("skills", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "agent_types",
        sa.Column("mcp_servers", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("agent_types", sa.Column("model", sa.String(64), nullable=True))
    op.add_column("agent_types", sa.Column("effort", sa.String(16), nullable=True))
    op.add_column("agent_types", sa.Column("harness", sa.String(32), nullable=True))

    # --- 2. agent_instances -------------------------------------------------
    op.create_table(
        "agent_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("handle", sa.String(length=64), nullable=False),
        sa.Column("type_name", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "handle", name="uq_agent_instance_handle"),
    )
    op.create_index(
        op.f("ix_agent_instances_project_id"), "agent_instances", ["project_id"]
    )

    # --- 3. who works where -------------------------------------------------
    op.add_column("topics", sa.Column("agent_instance_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_topics_agent_instance_id"), "topics", ["agent_instance_id"]
    )
    op.create_foreign_key(
        "fk_topics_agent_instance_id",
        "topics",
        "agent_instances",
        ["agent_instance_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "projects", sa.Column("default_agent_instance_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_projects_default_agent_instance_id",
        "projects",
        "agent_instances",
        ["default_agent_instance_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _carry_over_personas()
    op.drop_column("projects", "expert_role")

    _refuse_if_populated("expert_roles")
    op.drop_index("ix_expert_roles_project_id", table_name="expert_roles")
    op.drop_index("ix_expert_roles_name", table_name="expert_roles")
    op.drop_table("expert_roles")


def _carry_over_personas() -> None:
    """Give every project that had an ``expert_role`` a default agent wearing it.

    Under the handle the project's 芝士 was already writing memory under, so the
    instance adopts that pool rather than starting empty. Projects with no
    persona are left alone: they resolve to the same implicit 芝士 they had, and
    a row invented for them would only be a row to keep in sync.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, expert_role FROM projects WHERE expert_role IS NOT NULL "
            "AND expert_role <> ''"
        )
    ).all()
    for project_id, expert_role in rows:
        # uuid4 here rather than gen_random_uuid(): that function is only
        # built in from PG 13, and needing pgcrypto is not a thing to discover
        # during a deployment's migration.
        instance_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO agent_instances "
                "(id, project_id, handle, type_name, display_name, "
                " created_at, updated_at) "
                "VALUES (:id, :project_id, :handle, :type_name, "
                " :display_name, now(), now())"
            ),
            {
                "id": instance_id,
                "project_id": project_id,
                "handle": CHEESE_HANDLE,
                "type_name": expert_role,
                "display_name": CHEESE_NAME,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE projects SET default_agent_instance_id = :instance_id "
                "WHERE id = :project_id"
            ),
            {"instance_id": instance_id, "project_id": project_id},
        )


def downgrade() -> None:
    op.create_table(
        "expert_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("role_description", sa.Text(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("is_preset", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_expert_roles_name"), "expert_roles", ["name"])
    op.create_index(op.f("ix_expert_roles_project_id"), "expert_roles", ["project_id"])

    op.add_column(
        "projects", sa.Column("expert_role", sa.String(length=64), nullable=True)
    )
    op.execute(
        "UPDATE projects SET expert_role = i.type_name "
        "FROM agent_instances i WHERE i.id = projects.default_agent_instance_id"
    )
    op.drop_constraint(
        "fk_projects_default_agent_instance_id", "projects", type_="foreignkey"
    )
    op.drop_column("projects", "default_agent_instance_id")
    op.drop_constraint("fk_topics_agent_instance_id", "topics", type_="foreignkey")
    op.drop_index(op.f("ix_topics_agent_instance_id"), table_name="topics")
    op.drop_column("topics", "agent_instance_id")

    op.drop_index(op.f("ix_agent_instances_project_id"), table_name="agent_instances")
    op.drop_table("agent_instances")

    for column in ("harness", "effort", "model", "mcp_servers", "skills"):
        op.drop_column("agent_types", column)
    op.execute(
        "ALTER TABLE agent_types RENAME CONSTRAINT agent_types_name_key "
        "TO custom_roles_name_key"
    )
    op.execute("ALTER INDEX ix_agent_types_space_id RENAME TO ix_custom_roles_space_id")
    op.rename_table("agent_types", "custom_roles")
