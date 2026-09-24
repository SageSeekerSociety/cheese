"""库里再没有一行 ``configuration`` 带着模型、骨架、思考深度。

结论 3、28。代码那一侧的守卫在
``tests/unit/test_nothing_reads_the_model_off_a_type_or_an_instance.py``；这里
管的是**已经写进库里的行**——d7a91c4e2b60 那条迁移给每个 agent 都写了这六个键，
读点删光之后它们没有跟着消失，只是没人再看，而没人看的第二份声明正是「用哪个模
型」重新有两个住处的入口。
"""

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.domain.project.services import ProjectService
from tests.integration.conftest import registered

RETIRED = {"model", "harness", "effort"}

_A_ROLE = {
    "body": "只做安全评审",
    "skills": ["security"],
    "mcp_servers": ["github"],
}


def _migration():
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/b2f4d81a3c07_the_three_fields_leave_the_instance.py"
    )
    spec = importlib.util.spec_from_file_location("three_fields_leave", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_saved_configuration_still_carries_the_three_keys(db_session, _portal):
    migration = _migration()

    async def run():
        await registered(db_session, "owner")
        project = await ProjectService(db_session).create(
            owner_handle="owner", name="Three fields", forge_kind="github_app"
        )
        await db_session.flush()
        connection = await db_session.connection()

        def check(conn):
            migration.op = Operations(MigrationContext.configure(conn))
            # 迁移跑之前库里长这样：六个键，d7a91c4e2b60 写进去的那个形状。
            conn.execute(
                sa.text(
                    "UPDATE agent_instances SET configuration=CAST(:config AS json) "
                    "WHERE project_id=:project"
                ),
                {
                    "config": json.dumps(
                        {
                            **_A_ROLE,
                            "model": "opus",
                            "harness": "codex",
                            "effort": "high",
                        }
                    ),
                    "project": project.id,
                },
            )
            migration.upgrade()

            rows = (
                conn.execute(sa.text("SELECT configuration FROM agent_instances"))
                .scalars()
                .all()
            )
            assert rows, "库里一行 agent 都没有——这条断言没有检查过任何东西。"
            leftover = [row for row in rows if RETIRED & set(row)]
            assert not leftover, (
                f"这些行还带着模型/骨架/思考深度：{leftover}。"
                "模型绑在活上，骨架是部署设置，实例上不该留第二份声明。"
            )

            kept = conn.execute(
                sa.text(
                    "SELECT configuration FROM agent_instances "
                    "WHERE project_id=:project"
                ),
                {"project": project.id},
            ).scalar_one()
            assert kept == _A_ROLE, "角色那三个键必须原样留下，迁移只拿走三个。"

        await connection.run_sync(check)

    _portal.call(run)
