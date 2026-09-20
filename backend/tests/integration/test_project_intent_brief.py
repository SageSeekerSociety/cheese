"""建项目时写的那一句「你打算做什么」去了哪里（#946 片 C）。

有值：新生的 root 房间里多一份 doc —— 署名 system，内容是那句话加一句下一步。
没值（或只有空白）：什么都不写，房间照旧是空的，由它自己的起手区块顶上。

判据取「房间里有没有那份 doc」，而不是「服务被调过」：这份简报以 system 的身份
落在块上，而块才是人打开房间时真正读到的东西。署名也要一起断言 —— 它由平台拼
出来，不是芝士说的话（``seed_brief_doc`` 的约定），署错了就等于凭空替芝士开口。
"""

from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType


def test_intent_becomes_the_newborn_rooms_brief(
    db_session: AsyncSession, _portal: BlockingPortal
):
    async def _run() -> None:
        from app.domain.block.repositories import BlockRepository
        from app.domain.project.services import ProjectService

        said = "帮我把这学期的课程材料整理成一份大纲"
        project = await ProjectService(db_session).create(
            name="这学期的课", intent=said
        )

        assert project.intent == said, "原话要存下来，下次打开项目还看得到"
        assert project.root_topic_id is not None
        doc = await BlockRepository(db_session).doc_root(project.root_topic_id)
        assert doc is not None, "说了要做什么的项目，房间该带着这句话开门"
        assert doc.author_type == AuthorType.system
        assert said in doc.content
        assert "下一步" in doc.content, "光有那句话，人还是不知道接下来该做什么"

    _portal.call(_run)


def test_no_intent_leaves_the_room_without_a_document(
    db_session: AsyncSession, _portal: BlockingPortal
):
    """不答这一问是允许的：空房间照常，没有半份空简报。"""

    async def _run() -> None:
        from app.domain.block.repositories import BlockRepository
        from app.domain.project.services import ProjectService

        project = await ProjectService(db_session).create(name="没说要做什么")

        assert project.intent == ""
        assert project.root_topic_id is not None
        assert await BlockRepository(db_session).doc_root(project.root_topic_id) is None

    _portal.call(_run)


def test_whitespace_only_intent_is_not_an_intent(
    db_session: AsyncSession, _portal: BlockingPortal
):
    """空格不是答案。滑过输入框敲了个空格的人，不该得到一个空白的房间。

    ``_intent_brief`` 自己 strip 一次，不指望调用方去猜 —— 前端也 trim 了，但两边
    各修各的，删掉任意一边都不该改变结果。
    """

    async def _run() -> None:
        from app.domain.block.repositories import BlockRepository
        from app.domain.project.services import ProjectService

        project = await ProjectService(db_session).create(
            name="空白", intent="   \n\t "
        )

        assert project.root_topic_id is not None
        assert await BlockRepository(db_session).doc_root(project.root_topic_id) is None

    _portal.call(_run)


def test_the_answer_survives_the_http_round_trip(client):
    """那句话要从请求体走到项目行上，再从响应里回来。

    上面三条测的是 ``ProjectService.create`` 本身，证明不了这个字段接在路由上：
    中间任何一层漏传它，服务层的用例照样绿，而人写的那句话在半路上就没了。
    """

    said = "帮我把这学期的课程材料整理成一份大纲"
    created = client.post("/projects", json={"name": "这学期的课", "intent": said})

    assert created.status_code == 200
    assert created.json()["data"]["intent"] == said


def test_a_request_that_never_says_still_creates_the_project(client):
    """不答这一问是允许的——请求里根本没有这个键，项目照建，房间照旧。"""

    created = client.post("/projects", json={"name": "没答这一问的项目"})

    assert created.status_code == 200
    assert created.json()["data"]["intent"] == ""
    assert created.json()["data"]["root_topic_id"] is not None
