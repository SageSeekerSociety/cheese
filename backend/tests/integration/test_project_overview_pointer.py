"""项目的那一行记着它的目标文档，而写下这个编号的那一次读回得到。

目标文档这一摊从 ``ProjectService`` 搬进 ``living_doc.overview`` 之后，编号是**走裸表**
写进 ``projects.overview_document_id`` 的（导 ``project.models`` 回来会成环）。代价是
调用方手上那一行 ``Project`` 不再被就地刷新，所以这里钉住两件事：写下的编号确实是后面
都按它走的那个，以及再问一次答的是同一份、不是又造一份。

每次读的人都是重新按 id 读的：对话装配那条路是从自己查出来的 ``Project`` 上拿，
``POST /projects`` 答的 ``ProjectOut`` 本来也不带这一列（见 ``project.schemas``）。
"""

from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.overview import overview_document, seed_overview
from tests.integration.conftest import project_service, registered


async def test_the_goals_document_the_row_names_is_the_one_that_was_written(
    db_session: AsyncSession, _portal: BlockingPortal, stub_project_forge
):
    async def _run() -> None:
        await registered(db_session, "pointer-owner")
        projects = project_service(db_session)
        project = await projects.create(name="P", owner_handle="pointer-owner")

        doc = await overview_document(db_session, project.id)
        assert doc.version == 0, "第一次要它：空的一份，等着被写"

        await seed_overview(db_session, project.id, "# 目标\n\n先做这个")
        # 再问一次：还是那一份，版本跟着上去了 —— 不是又造了一份空的。
        again = await overview_document(db_session, project.id)
        assert again.id == doc.id, "两次读到的必须是同一份文档"
        assert again.version == 1
        assert "先做这个" in again.content

    _portal.call(_run)
