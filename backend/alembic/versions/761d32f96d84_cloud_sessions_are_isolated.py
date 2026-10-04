"""Sessions on Cloud machines are `isolated`

Each session's executor on a Cloud machine now runs in a sandbox of its own
(#2320), so a Cloud machine binds its topics `isolated`
(`device.supply.binding_visibility`). The bindings and devices already
written said `host`; this rewrites them to what is now true.

Data only. The previous image reads the topic binding's visibility only for a
self-hosted device's topic, and the device column not at all, so it runs
unchanged on the rewritten rows.

Revision ID: 761d32f96d84
Revises: da05dacf50ae
"""

from alembic import op

revision = "761d32f96d84"
down_revision = "da05dacf50ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE device_topic SET visibility = 'isolated' "
        "WHERE device_id IN (SELECT device_id FROM device WHERE supply = 'cloud')"
    )
    op.execute("UPDATE device SET visibility = 'isolated' WHERE supply = 'cloud'")


def downgrade() -> None:
    op.execute(
        "UPDATE device_topic SET visibility = 'host' "
        "WHERE device_id IN (SELECT device_id FROM device WHERE supply = 'cloud')"
    )
    op.execute("UPDATE device SET visibility = 'host' WHERE supply = 'cloud'")
