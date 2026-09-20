"""Store the room's resource choice independently of project favorites."""

import sqlalchemy as sa

from alembic import op

revision = "e2c7a4b97310"
down_revision = "d8a6b5c4e731"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("topics", sa.Column("compute_config", sa.JSON(), nullable=True))
    # Preserve existing selections as explicit project defaults before retiring
    # the team default and implicit room-to-project writes.
    op.execute("""
        UPDATE projects AS p SET settings =
          (COALESCE(p.settings::jsonb, '{}'::jsonb) - 'compute_profile') ||
          jsonb_build_object('compute_configs', jsonb_build_object(
            'default', jsonb_build_object(
              'name', CASE WHEN COALESCE(p.settings::jsonb->>'compute_profile', t.compute_profile) = 'cloud'
                     THEN '云端 · 标准配置' ELSE '自有设备 · 自动选择' END,
              'profile', COALESCE(p.settings::jsonb->>'compute_profile', t.compute_profile)),
            'favorites', '[]'::jsonb))
        FROM team AS t WHERE p.team_id = t.id
          AND NOT COALESCE(p.settings::jsonb, '{}'::jsonb) ? 'compute_configs'
          AND COALESCE(p.settings::jsonb->>'compute_profile', t.compute_profile) IN ('cloud', 'device')
    """)
    op.execute("""
        UPDATE projects SET settings =
          (settings::jsonb - 'compute_profile') || jsonb_build_object('compute_configs',
          jsonb_build_object('default', jsonb_build_object(
            'name', CASE WHEN settings->>'compute_profile' = 'cloud'
                   THEN '云端 · 标准配置' ELSE '自有设备 · 自动选择' END,
            'profile', settings->>'compute_profile'), 'favorites', '[]'::jsonb))
        WHERE settings::jsonb ? 'compute_profile' AND NOT settings::jsonb ? 'compute_configs'
          AND settings->>'compute_profile' IN ('cloud', 'device')
    """)
    op.execute(
        "UPDATE projects SET settings = settings::jsonb - 'compute_profile' WHERE settings::jsonb ? 'compute_profile'"
    )
    op.drop_column("team", "compute_profile")


def downgrade():
    op.add_column("team", sa.Column("compute_profile", sa.String(64), nullable=True))
    op.execute("""UPDATE projects SET settings = settings::jsonb ||
        jsonb_build_object('compute_profile', settings->'compute_configs'->'default'->>'profile')
        WHERE settings::jsonb ? 'compute_configs'""")
    op.drop_column("topics", "compute_config")
