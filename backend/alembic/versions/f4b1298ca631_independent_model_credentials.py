"""Credential ownership and member references, independent of execution devices.

Revision ID: f4b1298ca631
Revises: e7a4913c62bf
"""

import logging
import time

from alembic import op
import sqlalchemy as sa
from migration_helpers import with_lock_retries

revision = "f4b1298ca631"
down_revision = "e7a4913c62bf"
branch_labels = None
depends_on = None


def _create_schema():
    with_lock_retries('claude_logins, account_model_connections, device, team, "user"')
    op.create_table(
        "model_credential_hosts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("token", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_index(
        "ix_model_credential_hosts_token",
        "model_credential_hosts",
        ["token"],
        unique=True,
    )
    for table, column, kind, target, delete in [
        ("claude_logins", "team_id", sa.BigInteger(), "team", "CASCADE"),
        ("claude_logins", "owner_user_id", sa.Integer(), "user", "CASCADE"),
        (
            "claude_logins",
            "host_id",
            sa.String(64),
            "model_credential_hosts",
            "CASCADE",
        ),
        ("account_model_connections", "owner_user_id", sa.Integer(), "user", "CASCADE"),
    ]:
        op.add_column(table, sa.Column(column, kind, nullable=True))
        name = f"fk_{table}_{column}_{target}"
        op.create_foreign_key(
            name,
            table,
            target,
            [column],
            ["id"],
            ondelete=delete,
            postgresql_not_valid=True,
        )
    op.alter_column(
        "claude_logins", "device_id", existing_type=sa.String(64), nullable=True
    )
    op.drop_constraint(
        "fk_claude_logins_device_id_device", "claude_logins", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_claude_logins_device_id_device",
        "claude_logins",
        "device",
        ["device_id"],
        ["device_id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.create_table(
        "personal_model_resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "team.id",
                ondelete="CASCADE",
                name="fk_personal_model_resources_team_id_team",
            ),
            nullable=False,
        ),
        sa.Column(
            "login_id",
            sa.String(64),
            sa.ForeignKey(
                "claude_logins.id",
                ondelete="CASCADE",
                name="fk_personal_model_resources_login_id_claude_logins",
            ),
        ),
        sa.Column(
            "account_connection_id",
            sa.Uuid(),
            sa.ForeignKey(
                "account_model_connections.id",
                ondelete="CASCADE",
                name="fk_personal_model_resources_account_connection",
            ),
        ),
        sa.CheckConstraint(
            "(login_id IS NULL) <> (account_connection_id IS NULL)",
            name="ck_personal_model_resources_credential",
        ),
        sa.UniqueConstraint(
            "team_id", "login_id", name="uq_personal_model_resources_team_login"
        ),
        sa.UniqueConstraint(
            "team_id",
            "account_connection_id",
            name="uq_personal_model_resources_team_account",
        ),
    )
    op.create_index(
        "ix_personal_model_resources_team_id", "personal_model_resources", ["team_id"]
    )
    op.create_index(
        "ix_personal_model_resources_login_id", "personal_model_resources", ["login_id"]
    )
    op.create_index(
        "ix_personal_model_resources_account_connection_id",
        "personal_model_resources",
        ["account_connection_id"],
    )
    # Only the serving previous release needs this device-derived insert. The
    # new backend registers a login's team explicitly and never derives it.
    op.execute("""
        CREATE FUNCTION credential_login_rollout() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.device_id IS NOT NULL THEN
                INSERT INTO model_credential_hosts (id, token, name)
                SELECT device_id, token, name FROM device WHERE device_id = NEW.device_id
                ON CONFLICT DO NOTHING;
                NEW.host_id := NEW.device_id;
            END IF;
            IF NEW.team_id IS NULL AND NEW.device_id IS NOT NULL THEN
                SELECT d.team_id, t.personal_owner_user_id INTO NEW.team_id, NEW.owner_user_id
                FROM device d JOIN team t ON t.id = d.team_id WHERE d.device_id = NEW.device_id;
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER credential_login_rollout BEFORE INSERT ON claude_logins FOR EACH ROW EXECUTE FUNCTION credential_login_rollout()"
    )
    op.execute("""
        CREATE FUNCTION credential_account_rollout() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.owner_user_id IS NULL THEN
                SELECT personal_owner_user_id INTO NEW.owner_user_id FROM team WHERE id = NEW.team_id;
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER credential_account_rollout BEFORE INSERT ON account_model_connections FOR EACH ROW EXECUTE FUNCTION credential_account_rollout()"
    )


def _ensure_index(table, column):
    name = f"ix_{table}_{column}"
    # Same invalid-index guard as c7e2a91f4d3b. Concurrent builds can wait for
    # old serving-backend snapshots; retain lock_timeout and retry lock failures
    # with the bounded attempts/backoff used by with_lock_retries.
    for attempt in range(100):
        try:
            invalid = (
                op.get_bind()
                .execute(
                    sa.text(
                        "SELECT NOT i.indisvalid FROM pg_index i JOIN pg_class c "
                        "ON c.oid = i.indexrelid WHERE c.relname = :name"
                    ),
                    {"name": name},
                )
                .scalar()
            )
            if invalid:
                op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(
                name, table, [column], postgresql_concurrently=True, if_not_exists=True
            )
            return
        except sa.exc.DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "55P03" or attempt == 99:
                raise
            logging.getLogger("alembic.runtime.migration").warning(
                "Concurrent index %s hit lock_timeout; retry %s/100", name, attempt + 1
            )
            time.sleep(0.2)


def upgrade():
    # Schema DDL commits atomically when the autocommit block starts. A failed
    # concurrent index can leave that whole phase committed without a revision
    # stamp; retry the remaining idempotent work without recreating its tables.
    if not sa.inspect(op.get_bind()).has_table("model_credential_hosts"):
        _create_schema()
    # Release DDL locks before backfill; triggers already cover old inserts.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE claude_logins VALIDATE CONSTRAINT fk_claude_logins_device_id_device"
        )
        op.execute(
            "ALTER TABLE claude_logins VALIDATE CONSTRAINT fk_claude_logins_team_id_team"
        )
        op.execute(
            "ALTER TABLE claude_logins VALIDATE CONSTRAINT fk_claude_logins_owner_user_id_user"
        )
        op.execute(
            "ALTER TABLE claude_logins VALIDATE CONSTRAINT fk_claude_logins_host_id_model_credential_hosts"
        )
        op.execute(
            "ALTER TABLE account_model_connections VALIDATE CONSTRAINT fk_account_model_connections_owner_user_id_user"
        )
        for table, column in [
            ("claude_logins", "team_id"),
            ("claude_logins", "owner_user_id"),
            ("claude_logins", "host_id"),
            ("account_model_connections", "owner_user_id"),
        ]:
            _ensure_index(table, column)
        op.execute("""
            INSERT INTO model_credential_hosts (id, token, name)
            SELECT device_id, token, name FROM device d WHERE EXISTS
            (SELECT 1 FROM claude_logins l WHERE l.device_id = d.device_id)
            ON CONFLICT DO NOTHING
        """)
        op.execute(
            "UPDATE claude_logins SET host_id = device_id WHERE host_id IS NULL AND device_id IS NOT NULL"
        )
        op.execute("""
            UPDATE claude_logins l SET team_id = d.team_id, owner_user_id = t.personal_owner_user_id
            FROM device d JOIN team t ON t.id = d.team_id
            WHERE l.device_id = d.device_id AND l.team_id IS NULL
        """)
        op.execute("""
            UPDATE account_model_connections c SET owner_user_id = t.personal_owner_user_id
            FROM team t WHERE t.id = c.team_id AND c.owner_user_id IS NULL
            AND t.personal_owner_user_id IS NOT NULL
        """)
        # Restore only the original owner's credentials. These agents and their
        # histories were retained by P3; no key or login is copied or resealed.
        for column, table in [
            ("login_id", "claude_logins"),
            ("account_connection_id", "account_model_connections"),
        ]:
            op.execute(f"""
                INSERT INTO personal_model_resources (id, team_id, {column})
                SELECT gen_random_uuid(), p.team_id, c.id FROM own_agents o
                JOIN agent_instances a ON a.id = o.instance_id
                JOIN projects p ON p.id = a.project_id
                JOIN {table} c ON c.id = o.{column}
                WHERE c.owner_user_id = o.owner_user_id AND c.team_id <> p.team_id
                AND EXISTS (SELECT 1 FROM team_user_relation r WHERE r.team_id = p.team_id
                    AND r.user_id = o.owner_user_id AND r.deleted_at IS NULL)
                ON CONFLICT (team_id, {column}) DO NOTHING
            """)

        # Legacy service secrets stay on their computers. Owners re-add keys
        # through Models; dev had zero legacy rows at the read-only preflight.
        op.execute(
            "DELETE FROM claude_logins WHERE auth_method IN ('console', 'model_service')"
        )


def downgrade():
    raise RuntimeError("Member credential references must be retained")
