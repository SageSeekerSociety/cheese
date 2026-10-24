"""Backfill each device's sole team, refusing ambiguous ownership."""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "c9e25a7b38f1"
down_revision = "b8d14f6a27e0"
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    ambiguous = (
        db.execute(
            sa.text(
                "SELECT device_id FROM device_team GROUP BY device_id HAVING count(*) > 1 ORDER BY device_id"
            )
        )
        .scalars()
        .all()
    )
    if ambiguous:
        raise RuntimeError(
            f"Devices assigned to multiple teams: {', '.join(ambiguous)}"
        )
    op.execute(
        "UPDATE device d SET team_id = dt.team_id FROM device_team dt WHERE d.device_id = dt.device_id AND d.team_id IS NULL"
    )
    op.execute(
        "UPDATE device SET team_id = device_enrollment_personal_team(owner_user_id) WHERE team_id IS NULL"
    )
    with_lock_retries("device, team")
    op.create_foreign_key(
        "fk_device_team_id_team",
        "device",
        "team",
        ["team_id"],
        ["id"],
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE device ADD CONSTRAINT ck_device_team_present CHECK (team_id IS NOT NULL) NOT VALID"
    )
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE device VALIDATE CONSTRAINT fk_device_team_id_team")
        op.execute("ALTER TABLE device VALIDATE CONSTRAINT ck_device_team_present")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_device_team_id")
        op.execute("CREATE INDEX CONCURRENTLY ix_device_team_id ON device (team_id)")
    with_lock_retries("device")
    # migration-safety: allow alter-column-existing — validated CHECK avoids a scan under the DDL lock
    op.alter_column("device", "team_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_constraint("ck_device_team_present", "device", type_="check")


def downgrade():
    raise RuntimeError("Device ownership migration is forward-only")
