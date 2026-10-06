"""建项目时写的那一句「你打算做什么」去了哪里（#946 片 C）。

有值：新项目的总览写上第一版 —— 署名 system，内容是那句话加一句下一步。
没值（或只有空白）：什么都不写，总览照旧是空的。

判据取「总览里写了什么」，而不是「服务被调过」：总览是每段对话的 AI 队友都读的
那一份，也是人打开项目时读到的东西。署名也要一起断言 —— 它由平台拼出来，不是芝
士说的话，署错了就等于凭空替芝士开口。
"""

from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import post_project, registered


async def _owner(session) -> str:
    """Someone to own the project: it belongs to a team, here their personal one."""
    await registered(session, "intent-owner")
    return "intent-owner"


# 建项目现在还会顺手把代码托管仓库准备好（主分支的 forge 逻辑），所以服务层的用
# 例也要装上 ``stub_project_forge``：不装，create 会去够一个测试环境里不存在的
# Forgejo，报的是「此部署尚未配置项目代码托管服务」，和这一问毫无关系。


def test_intent_becomes_the_new_projects_overview(
    db_session: AsyncSession, _portal: BlockingPortal, stub_project_forge
):
    async def _run() -> None:
        from app.domain.project.services import ProjectService

        said = "帮我把这学期的课程材料整理成一份大纲"
        projects = ProjectService(db_session)
        project = await projects.create(
            name="这学期的课", intent=said, owner_handle=await _owner(db_session)
        )

        assert project.intent == said, "原话要存下来，下次打开项目还看得到"
        doc = await projects.overview_document(project)
        assert doc.version > 0, "说了要做什么的项目，总览该带着这句话开门"
        # 署名是「system」：平台产的，不是芝士，也不是某个人。
        assert doc.author == "system"
        assert said in doc.content
        assert "下一步" in doc.content, "光有那句话，人还是不知道接下来该做什么"

    _portal.call(_run)


def test_no_intent_leaves_the_overview_unwritten(
    db_session: AsyncSession, _portal: BlockingPortal, stub_project_forge
):
    """不答这一问是允许的：总览照常空着，没有半份空简报。"""

    async def _run() -> None:
        from app.domain.project.services import ProjectService

        projects = ProjectService(db_session)
        project = await projects.create(
            name="没说要做什么", owner_handle=await _owner(db_session)
        )

        assert project.intent == ""
        assert (await projects.overview_document(project)).version == 0

    _portal.call(_run)


def test_whitespace_only_intent_is_not_an_intent(
    db_session: AsyncSession, _portal: BlockingPortal, stub_project_forge
):
    """空格不是答案。滑过输入框敲了个空格的人，不该得到一份空白的总览。

    ``_intent_brief`` 自己 strip 一次，不指望调用方去猜 —— 前端也 trim 了，但两边
    各修各的，删掉任意一边都不该改变结果。
    """

    async def _run() -> None:
        from app.domain.project.services import ProjectService

        projects = ProjectService(db_session)
        project = await projects.create(
            name="空白", intent="   \n\t ", owner_handle=await _owner(db_session)
        )

        assert (await projects.overview_document(project)).version == 0

    _portal.call(_run)


def test_the_answer_survives_the_http_round_trip(client):
    """那句话要从请求体走到项目行上，再从响应里回来。

    上面三条测的是 ``ProjectService.create`` 本身，证明不了这个字段接在路由上：
    中间任何一层漏传它，服务层的用例照样绿，而人写的那句话在半路上就没了。
    """

    said = "帮我把这学期的课程材料整理成一份大纲"
    created = post_project(client, json={"name": "这学期的课", "intent": said})

    assert created.status_code == 200
    assert created.json()["data"]["intent"] == said


def test_a_request_that_never_says_still_creates_the_project(client):
    """不答这一问是允许的——请求里根本没有这个键，项目照建。"""

    created = post_project(client, json={"name": "没答这一问的项目"})

    assert created.status_code == 200
    assert created.json()["data"]["intent"] == ""
    assert created.json()["data"]["root_topic_id"] is not None
