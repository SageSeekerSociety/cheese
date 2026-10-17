"""项目的那一行记着它的目标文档，而写下这个编号的那一次读回得到。

目标文档这一摊从 ``ProjectService`` 搬进 ``living_doc.overview`` 之后，编号是**走裸表**
写进 ``projects.overview_document_id`` 的（导 ``project.models`` 回来会成环）。代价是
调用方手上那一行 ``Project`` 不再被就地刷新，所以这里钉住两件事：写下的编号确实是后面
都按它走的那个，以及再问一次答的是同一份、不是又造一份。

每次读的人都是重新按 id 读的：别人读这个指针走 ``pointed_overview``（它按 id 查编号，
不认手上那一行），``POST /projects`` 答的 ``ProjectOut`` 本来也不带这一列（见
``project.schemas``）。
"""

from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.overview import (
    overview_document,
    pointed_overview,
    seed_overview,
)
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


async def test_the_reader_answers_even_when_the_row_was_read_before_the_write(
    db_session: AsyncSession, _portal: BlockingPortal, stub_project_forge
):
    """别人读这个指针走 ``pointed_overview``：还没有就答 None，不顺手造一份。

    而且要在**手上已经有一行**的时候也答得对 —— 编号是裸表写下的，那一行上还是
    旧值。这条正是记忆搬迁踩过的坑：它先 ``get_or_404`` 拿到项目行，再按行上的编号
    去找目标文档，读到 None，报告就少了一节。
    """

    async def _run() -> None:
        await registered(db_session, "pointer-reader")
        service = project_service(db_session)
        project = await service.create(name="P", owner_handle="pointer-reader")
        assert await pointed_overview(db_session, project.id) is None, "还没有就是 None"

        doc = await overview_document(db_session, project.id)
        # 先把行读进这个会话，再写指针：写完之后那一行不作数了。
        await service.get_or_404(project.id)
        await seed_overview(db_session, project.id, "# 目标\n\n先做这个")
        seen = await pointed_overview(db_session, project.id)
        assert seen is not None, "行上有旧值不影响这里"
        assert seen.id == doc.id, "答的是那一行指的那份"
        assert seen.version == 1

    _portal.call(_run)
