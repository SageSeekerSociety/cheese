"""A machine holds any number of Claude Code logins, each its own connection

Revision ID: e81c7f29ab40
Revises: 8f2c5d1a9b47
Create Date: 2026-10-18

Until now a machine had one login for its owner's own Claude Code
(`device_claude_login`, keyed by the machine) and an own agent named the
machine it ran on (`own_agents.device_id`). A machine now holds several, so
each login gets a record of its own (`claude_logins`, unique per machine and
what the machine calls it, ``local_id``) and an own agent names its login
(`own_agents.login_id`).

The login a machine already has is its first login, ``local_id = 'default'``
(on the machine it stays in its directory: on macOS its Keychain item is named
after that path), and its record keeps the machine's id, which named it until
now. So every own agent points at the login it already ran on, and nobody logs
in again.

`device_claude_login` and `own_agents.device_id` stay for this release: a
deploy migrates while the previous backend still serves, and that backend reads
and writes them. Two triggers carry its writes over:

- a write to `device_claude_login` reaches the machine's first login in
  `claude_logins`. What the machine reported is copied; a removal or a name is
  copied only when that write changed it, so a stale row never undoes a
  removal made here. A login removed here stays logged out whatever the
  previous backend writes.
- an own agent the previous backend adds or moves by `device_id` gets that
  machine's first login.

Writes made here are not copied back: nothing of this release reads the old
table. The drop of both, and of the triggers, is the next release's (rule 4 of
`.claude/rules/migrations.md`).

The invalid-index cleanup names `own_agents`, which the device connection owner
maps, so the deploy drains that owner across this migration and recreates it on
this release (deploy/migration-touches.py): on connect it is the owner that asks
a machine for its logins, and an owner on the previous release would ask for
the first one only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e81c7f29ab40"
down_revision: str | Sequence[str] | None = "8f2c5d1a9b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The first login is 'default' in the SQL below:
#: `app.domain.device.login_kind.FIRST_LOGIN` when this was written.
INDEX = "ix_own_agents_login_id"


def _invalid(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    with_lock_retries("device, device_claude_login, own_agents")
    op.create_table(
        "claude_logins",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("local_id", sa.String(64), nullable=False),
        sa.Column("installed", sa.Boolean(), nullable=False),
        sa.Column("logged_in", sa.Boolean(), nullable=False),
        sa.Column("auth_method", sa.String(32), nullable=True),
        sa.Column("subscription_type", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("service_host", sa.String(255), nullable=True),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_claude_logins"),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.device_id"],
            ondelete="CASCADE",
            name="fk_claude_logins_device_id_device",
        ),
        sa.UniqueConstraint(
            "device_id", "local_id", name="uq_claude_logins_device_id_local_id"
        ),
    )
    op.execute(
        """
        INSERT INTO claude_logins
            (id, device_id, local_id, installed, logged_in, auth_method,
             subscription_type, model, service_host, checked_at, name, revoked_at)
        SELECT device_id, device_id, 'default', installed, logged_in, auth_method,
               subscription_type, model, service_host, checked_at, name, revoked_at
        FROM device_claude_login
        """
    )
    op.add_column("own_agents", sa.Column("login_id", sa.String(64), nullable=True))
    op.execute(
        """
        UPDATE own_agents o SET login_id = l.id
        FROM claude_logins l
        WHERE l.id = o.device_id AND o.login_id IS NULL
        """
    )
    op.execute(
        """
        CREATE FUNCTION claude_login_from_previous_release() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            revoke_changed boolean := TG_OP = 'INSERT'
                OR NEW.revoked_at IS DISTINCT FROM OLD.revoked_at;
            name_changed boolean := TG_OP = 'INSERT'
                OR NEW.name IS DISTINCT FROM OLD.name;
        BEGIN
            INSERT INTO claude_logins AS l
                (id, device_id, local_id, installed, logged_in, auth_method,
                 subscription_type, model, service_host, checked_at, name,
                 revoked_at)
            VALUES (NEW.device_id, NEW.device_id, 'default', NEW.installed,
                    NEW.logged_in AND NEW.revoked_at IS NULL, NEW.auth_method,
                    NEW.subscription_type, NEW.model, NEW.service_host,
                    NEW.checked_at, NEW.name, NEW.revoked_at)
            ON CONFLICT (device_id, local_id) DO UPDATE SET
                installed = EXCLUDED.installed,
                auth_method = EXCLUDED.auth_method,
                subscription_type = EXCLUDED.subscription_type,
                model = EXCLUDED.model,
                service_host = EXCLUDED.service_host,
                checked_at = EXCLUDED.checked_at,
                name = CASE WHEN name_changed THEN EXCLUDED.name ELSE l.name END,
                revoked_at = CASE WHEN revoke_changed THEN EXCLUDED.revoked_at
                                  ELSE l.revoked_at END,
                logged_in = EXCLUDED.logged_in AND (
                    CASE WHEN revoke_changed THEN EXCLUDED.revoked_at
                         ELSE l.revoked_at END
                ) IS NULL;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER claude_login_from_previous_release
        AFTER INSERT OR UPDATE ON device_claude_login
        FOR EACH ROW EXECUTE FUNCTION claude_login_from_previous_release()
        """
    )
    op.execute(
        """
        CREATE FUNCTION own_agent_login_from_previous_release() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND NEW.login_id IS NOT NULL THEN
                RETURN NEW;
            END IF;
            NEW.login_id := (
                SELECT id FROM claude_logins
                WHERE device_id = NEW.device_id AND local_id = 'default'
            );
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER own_agent_login_from_previous_release
        BEFORE INSERT OR UPDATE OF device_id ON own_agents
        FOR EACH ROW EXECUTE FUNCTION own_agent_login_from_previous_release()
        """
    )
    with op.get_context().autocommit_block():
        if _invalid(INDEX):
            op.drop_index(INDEX, table_name="own_agents", postgresql_concurrently=True)
        op.create_index(
            INDEX,
            "own_agents",
            ["login_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
    with_lock_retries("own_agents, claude_logins")
    op.create_foreign_key(
        "fk_own_agents_login_id_claude_logins",
        "own_agents",
        "claude_logins",
        ["login_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_own_agents_login_connection",
        "own_agents",
        "login_id IS NULL OR account_connection_id IS NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE own_agents"
            " VALIDATE CONSTRAINT fk_own_agents_login_id_claude_logins"
        )
        op.execute(
            "ALTER TABLE own_agents VALIDATE CONSTRAINT ck_own_agents_login_connection"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER own_agent_login_from_previous_release ON own_agents")
    op.execute("DROP FUNCTION own_agent_login_from_previous_release()")
    op.execute("DROP TRIGGER claude_login_from_previous_release ON device_claude_login")
    op.execute("DROP FUNCTION claude_login_from_previous_release()")
    op.drop_constraint("ck_own_agents_login_connection", "own_agents", type_="check")
    op.drop_constraint(
        "fk_own_agents_login_id_claude_logins", "own_agents", type_="foreignkey"
    )
    op.drop_index(INDEX, table_name="own_agents")
    op.drop_column("own_agents", "login_id")
    op.drop_table("claude_logins")
