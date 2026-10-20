"""Retire device lending after the team-owned runtime has deployed."""

from migration_helpers import with_lock_retries

from alembic import op

revision = "d1f37c8a46b2"
down_revision = "c9e25a7b38f1"
branch_labels = None
depends_on = None


def upgrade():
    # Finish these idempotent updates before taking the short-lived DDL locks.
    # The deployed runtime already saves only team_devices.
    with op.get_context().autocommit_block():
        op.execute("""
            UPDATE agent_environments
            SET environment = jsonb_set(environment, '{spec}', '"team_devices"')
            WHERE environment->>'kind' = 'sandbox'
              AND environment->>'spec' ~ '^devices_of:[0-9]+$'
        """)
        op.execute("""
            UPDATE topics
            SET default_environment = jsonb_set(default_environment::jsonb, '{spec}', '"team_devices"')::json
            WHERE default_environment->>'kind' = 'sandbox'
              AND default_environment->>'spec' ~ '^devices_of:[0-9]+$'
        """)
        op.execute("""
            UPDATE tasks
            SET default_environment = jsonb_set(default_environment, '{spec}', '"team_devices"')
            WHERE default_environment->>'kind' = 'sandbox'
              AND default_environment->>'spec' ~ '^devices_of:[0-9]+$'
        """)
        op.execute("""
            UPDATE projects
            SET settings = jsonb_set(settings::jsonb, '{execution_environment,spec}', '"team_devices"')::json
            WHERE settings->'execution_environment'->>'kind' = 'sandbox'
              AND settings->'execution_environment'->>'spec' ~ '^devices_of:[0-9]+$'
        """)
        op.execute("""
            UPDATE sandbox_homes SET spec = 'team_devices'
            WHERE spec ~ '^devices_of:[0-9]+$'
        """)
    with_lock_retries("device, device_team, device_project")
    op.execute("DROP TRIGGER device_legacy_team_write ON device_team")
    op.execute("DROP TRIGGER device_enrollment_team_default ON device")
    op.execute("DROP FUNCTION device_legacy_team_write()")
    op.execute("DROP FUNCTION device_enrollment_team_default()")
    op.execute("DROP FUNCTION device_enrollment_personal_team(integer)")
    op.drop_table("device_project")
    op.drop_table("device_team")


def downgrade():
    raise RuntimeError("Device ownership cleanup is forward-only")
