"""A device belongs to one team; retain enrollment provenance.

The old backend serves during upgrade. Its inserts get a personal team until
the new backend takes over; the next release removes that rollout trigger.
"""

from alembic import op
import sqlalchemy as sa
from migration_helpers import with_lock_retries

revision = "b8d14f6a27e0"
down_revision = "5d3e8a1f2b7c"
branch_labels = None
depends_on = None


def upgrade():
    with_lock_retries('device, hosted_device, "user", team')
    op.add_column("device", sa.Column("team_id", sa.BigInteger(), nullable=True))
    # Provenance cannot cascade-delete a shared team's resource.
    for table in ("device", "hosted_device"):
        op.alter_column(table, "owner_user_id", existing_type=sa.Integer(), nullable=True)
        op.drop_constraint(f"{table}_owner_user_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(f"fk_{table}_owner_user_id_user", table, "user", ["owner_user_id"], ["id"], ondelete="SET NULL", postgresql_not_valid=True)
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE device VALIDATE CONSTRAINT fk_device_owner_user_id_user")
        op.execute("ALTER TABLE hosted_device VALIDATE CONSTRAINT fk_hosted_device_owner_user_id_user")
    op.execute('''
        CREATE FUNCTION device_enrollment_personal_team(enroller integer) RETURNS bigint
        LANGUAGE plpgsql AS $$
        DECLARE chosen bigint;
        BEGIN
            INSERT INTO team (id, name, intro, description, avatar_id, personal_owner_user_id, created_at, updated_at)
            VALUES (nextval('team_seq'), '个人', '', '', 0, enroller, now(), now())
            ON CONFLICT (personal_owner_user_id) WHERE personal_owner_user_id IS NOT NULL AND deleted_at IS NULL DO NOTHING;
            SELECT id INTO STRICT chosen FROM team WHERE personal_owner_user_id = enroller AND deleted_at IS NULL;
            INSERT INTO team_user_relation (id, team_id, user_id, role, created_at, updated_at)
            SELECT nextval('team_user_relation_seq'), chosen, enroller, 0, now(), now()
            WHERE NOT EXISTS (SELECT 1 FROM team_user_relation WHERE team_id = chosen AND user_id = enroller AND deleted_at IS NULL);
            RETURN chosen;
        END $$;
    ''')
    op.execute('''
        CREATE FUNCTION device_enrollment_team_default() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.team_id IS NULL THEN
                NEW.team_id := device_enrollment_personal_team(NEW.owner_user_id);
            END IF;
            RETURN NEW;
        END $$;
    ''')
    op.execute('''
        CREATE TRIGGER device_enrollment_team_default BEFORE INSERT ON device
        FOR EACH ROW EXECUTE FUNCTION device_enrollment_team_default();
    ''')
    # The prior backend may still finish a team-assignment request before it
    # is replaced. Retired with the old tables in the following release.
    op.execute('''
        CREATE FUNCTION device_legacy_team_write() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE machine text; enroller integer; chosen bigint;
        BEGIN
            machine := CASE WHEN TG_OP = 'DELETE' THEN OLD.device_id ELSE NEW.device_id END;
            SELECT owner_user_id INTO enroller FROM device WHERE device_id = machine FOR UPDATE;
            IF NOT FOUND THEN RETURN NULL; END IF;
            IF (SELECT count(*) FROM device_team WHERE device_id = machine) > 1 THEN
                RAISE EXCEPTION 'Device assigned to multiple teams: %', machine;
            END IF;
            SELECT team_id INTO chosen FROM device_team WHERE device_id = machine;
            IF chosen IS NULL THEN chosen := device_enrollment_personal_team(enroller); END IF;
            UPDATE device SET team_id = chosen WHERE device_id = machine;
            RETURN NULL;
        END $$;
    ''')
    op.execute('''
        CREATE TRIGGER device_legacy_team_write AFTER INSERT OR UPDATE OR DELETE ON device_team
        FOR EACH ROW EXECUTE FUNCTION device_legacy_team_write();
    ''')


def downgrade():
    raise RuntimeError("Device ownership migration is forward-only")
