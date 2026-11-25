"""team_feishu_apps：一个团队连接成员飞书用的那一个企业自建应用

Revision ID: 4288e24987e2
Revises: fbdb95e20845
Create Date: 2026-11-24

企业自建应用只在它那家企业的飞书里能用，团队是持有它的那一层：公司的团队设公司的应用，
一个人自己用就设在他的个人团队上。每个团队至多一行（主键就是 ``team_id``），App Secret
密封、绑在团队上。

只建这张新表，不动任何已有的行，也不在这里加指向 ``team`` 的外键：外键在下一个迁移里
``NOT VALID`` 地加、再下一个里验证，免得建表时在 ``team`` 上拿锁、挡住团队的写入。
``IF NOT EXISTS``：中途断掉之后重跑不报 42P07。

**显式地把库降级到这一版之前会删掉这张表，各团队设好的飞书应用一并没了**（只回退镜像、
不降级数据库则不受影响）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4288e24987e2"
down_revision: str | Sequence[str] | None = "fbdb95e20845"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team_feishu_apps (
            team_id BIGINT NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            domain VARCHAR(16) NOT NULL,
            secret TEXT NOT NULL,
            updated_by VARCHAR(64) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT team_feishu_apps_pkey PRIMARY KEY (team_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS team_feishu_apps")
