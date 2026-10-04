"""没有项目记忆池：所有人都该看见的东西是一份文档（结论 7）。

一个共享池和一份文档差的不是存在哪儿。池子没有主、不留痕、人翻不到也改不了，
而它偏偏又是「大家都该知道的事」唯一的落点——于是项目的共识住在一个只有模型读
得到的地方。文档三样都有，所以那一路的去向是项目总览那个房间的实况文档。

写记忆的那个旧入口已经整个停用（话题「记忆机制照搬CC」）：先是 `cheese_remember
everyone` 往这份文档里追加的那一路——它把总览写成了只增不减的观察清单——接着是
`cheese_remember` 本身，它写的条目池已经不再注入任何地方。这一组守的是还成立的那
几头：停用要明说、并且说清该去哪写；别的房间跑一轮时，总览文档在它的提示词里；
迁移把旧项目池落进这份文档。

文档本身现在分三块，只有「项目是什么」是写的（#1889 第 1 条）：总览房间的一轮拿
得到 ②③（从话题、结论现拼），别的房间只拿到 ①。手抄进正文的副本谁都
读不到——写在别块的字一个字都不该进提示词。
"""

import importlib.util
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.integration.conftest import (
    chat_ws_url,
    post_message,
    post_project,
    registered,
    session_auth_headers,
)
from tests.support.living_doc import write_doc

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "a1c4e8f30b26_project_memory_becomes_the_overview_document.py"
)

FACT = "中期答辩定在 11 月 15 日，要现场演示一个能跑的 demo"


def _project_and_room(client) -> tuple[str, str]:
    project_id = post_project(client, json={"name": "P"}, owner="user-1").json()[
        "data"
    ]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "干活的房间"},
        headers=session_auth_headers("user-1"),
    ).json()["data"]["id"]
    return project_id, topic_id


def _overview_room(client, project_id: str) -> str:
    return client.get(f"/projects/{project_id}").json()["data"]["root_topic_id"]


def _say(client, topic_id: str, text: str = "@芝士 现在什么状态") -> None:
    """在这一轮里说一句话，等它跑完。提示词落在 `stub_hooks` 上。"""
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        post_message(client, topic_id, "user-1", {"content": text})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


def _doc_text(client, topic_id: str) -> str:
    doc = client.get(f"/topics/{topic_id}/doc").json()["data"]
    return (doc or {}).get("content", "")


def test_writing_memory_through_this_endpoint_is_retired_and_says_where(client):
    """写记忆的旧入口整个停用了，而且明说停在哪、该去哪写，不是静默吞掉。

    `scope="everyone"` 那一路先停：它把总览文档写成了一份只增不减的观察清单
    （话题「记忆机制照搬CC」）。剩下的写入面随后一起收掉——`cheese_remember` 写
    的是条目池，而条目池已经不再注入任何地方，所以「已记入」是一句谎话。旧会话
    里的调用方读到的这句要说清记忆现在是什么形状：`~/.cheese/memory/` 下的
    文件。总览文档只写「项目是什么」。
    """
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)

    for body in (
        {"content": FACT, "topic": topic_id, "scope": "everyone"},
        {"content": FACT, "scope": "everyone"},
        {"content": FACT, "topic": topic_id},
        {"content": FACT},
    ):
        refused = client.post(f"/projects/{project_id}/memory", json=body)
        assert refused.status_code == 422, refused.text
        assert "停用" in refused.text
        assert "~/.cheese/memory/" in refused.text
    assert FACT not in _doc_text(client, overview)


def test_another_room_reads_the_overview_document_on_its_next_turn(client, stub_hooks):
    """别的房间跑一轮，总览那一份在提示词里——而它自己的文档是另一份。"""
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)
    client.put(
        f"/topics/{overview}/doc",
        json={"content": FACT, "expected_version": 0},
        headers=session_auth_headers("user-1"),
    )

    _say(client, topic_id)

    assert stub_hooks.last_system_prompt is not None
    prompt = stub_hooks.told
    assert FACT in prompt


def _move_statements() -> tuple[str, ...]:
    """迁移真正会跑的那两句，从迁移模块里取，不照抄一份。"""
    spec = importlib.util.spec_from_file_location("_p36_move", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (module.SEED_OVERVIEW_DOC, module.APPEND_PROJECT_MEMORY_TO_OVERVIEW)


async def _run_migration(db_session: AsyncSession) -> None:
    for statement in _move_statements():
        await db_session.execute(sa.text(statement))
    await db_session.flush()


async def _legacy_memory(db_session, project_id, content):
    await db_session.execute(
        sa.text(
            "INSERT INTO memory_entries "
            "(id, scope, scope_id, content, layer, created_at, updated_at) "
            "VALUES (:id, 'project', :pool, :content, 'fact', now(), now())"
        ),
        {"id": uuid.uuid4(), "pool": str(project_id), "content": content},
    )


async def _legacy_contents(db_session, project_id):
    return list(
        (
            await db_session.execute(
                sa.text(
                    "SELECT content FROM memory_entries "
                    "WHERE scope = 'project' AND scope_id = :pool"
                ),
                {"pool": str(project_id)},
            )
        ).scalars()
    )


def test_the_migration_lands_every_project_pool_row_in_that_document(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """存量：``scope='project'`` 的每一条，迁移跑完都在目标文档里找得到同一份内容。

    连跑两遍——dev 先跑迁移后换容器，窗口里旧镜像还在往这个池写，这一条要由 P36b
    原样再跑一遍，所以第二遍不能把同一条再追加一次。行只搬不删：这批行不可再生
    （结论 61），文档写错了没有第二份可对照。
    """

    async def run() -> None:
        await registered(db_session, "andyl")
        project = await ProjectService(db_session).create(
            name="有总览文档的项目", owner_handle="andyl", forge_kind="github_app"
        )
        assert project.root_topic_id is not None
        await write_doc(
            db_session, project.root_topic_id, "## 目标\n做课程推荐系统", "andyl"
        )
        facts = ["数据来源是教务处脱敏数据", FACT]
        for fact in facts:
            await _legacy_memory(db_session, project.id, fact)
        await db_session.flush()

        for _ in range(2):
            await _run_migration(db_session)

            doc = await TopicService(db_session).get_doc(project.root_topic_id)
            assert doc is not None
            for fact in facts:
                assert doc.content.count(fact) == 1
            # 人自己写的那一段还在，被搬进来的那几条排在它后面。
            assert "做课程推荐系统" in doc.content
            # 只搬不删：窗口里旧镜像还在写这个池，删了就是孤儿。
            assert sorted(await _legacy_contents(db_session, project.id)) == sorted(
                facts
            )

    _portal.call(run)


def test_the_migration_builds_the_first_document_for_a_project_that_has_none(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """总览房间还没有文档的项目，这一步给它建一份，行落在里面。

    新建的项目都没有 doc 块，所以这是默认状态而不是边角情况。不搬，这些行就既不
    在文档里、也不在任何一个读点上——谁都读不到；而 P36b 要逐行核对全部对上才
    DELETE，漏一个项目那一档就永远删不掉。
    """

    async def run() -> None:
        await registered(db_session, "andyl")
        project = await ProjectService(db_session).create(
            name="没有总览文档的项目", owner_handle="andyl", forge_kind="github_app"
        )
        assert project.root_topic_id is not None
        assert await TopicService(db_session).get_doc(project.root_topic_id) is None
        await _legacy_memory(db_session, project.id, FACT)
        await db_session.flush()

        await _run_migration(db_session)

        doc = await TopicService(db_session).get_doc(project.root_topic_id)
        assert doc is not None
        assert doc.content.count(FACT) == 1
        # 建出来的第一版从标题开始，前面不带空行。
        assert doc.content.startswith("## ")
        # 只搬不删，和有文档的那一条一样。
        assert await _legacy_contents(db_session, project.id) == [FACT]

        # 窗口里原样再跑一遍：不再建第二份文档，也不再追加同一条。
        await _run_migration(db_session)
        again = await TopicService(db_session).get_doc(project.root_topic_id)
        assert again is not None and again.id == doc.id
        assert again.content.count(FACT) == 1

    _portal.call(run)


def test_the_overview_room_does_not_read_its_own_document_twice(client, stub_hooks):
    """总览房间自己那一轮，这份文档只出现一次。

    同一份状态在提示词里出现两遍，模型会把它当成两件事——两份还可能一新一旧。
    """
    project_id, _ = _project_and_room(client)
    overview = _overview_room(client, project_id)
    client.put(
        f"/topics/{overview}/doc",
        json={"content": FACT, "expected_version": 0},
        headers=session_auth_headers("user-1"),
    )

    _say(client, overview)

    assert stub_hooks.last_system_prompt is not None
    prompt = stub_hooks.told
    assert prompt.count(FACT) == 1


def test_the_overview_room_reads_the_other_blocks_from_the_data(client, stub_hooks):
    """总览房间那一轮，②③ 现拼：话题、结论都不在文档正文里。

    这一块有个前提：注入的那一份必须**不是**文档原文。人写进正文的副本，和平台
    从结构化数据拼的那一份，是两个版本；一旦拼接，读到的人分不出哪个算数。
    """
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)
    client.put(
        f"/topics/{overview}/doc",
        json={
            "content": "## 项目是什么\n\n给高中生做算法课。\n\n"
            "## 现在在做什么\n\n- 这一条是手抄的，不算数。\n",
            "expected_version": 0,
        },
        headers=session_auth_headers("user-1"),
    )

    _say(client, overview)

    assert stub_hooks.last_system_prompt is not None
    prompt = stub_hooks.told
    assert "给高中生做算法课" in prompt
    assert "## 现在在做什么" in prompt
    assert "干活的房间" in prompt
    # 手抄进正文的那一份谁都读不到：写在那儿等于没写。
    assert "这一条是手抄的" not in prompt


def test_another_room_gets_only_what_the_project_is(client, stub_hooks):
    """别的房间只注入 ①，②③ 要哪一块自己查——不必每轮往每间房塞项目快照。"""
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)
    client.put(
        f"/topics/{overview}/doc",
        json={
            "content": "## 项目是什么\n\n给高中生做算法课。\n",
            "expected_version": 0,
        },
        headers=session_auth_headers("user-1"),
    )

    _say(client, topic_id)

    assert stub_hooks.last_system_prompt is not None
    prompt = stub_hooks.told
    assert "给高中生做算法课" in prompt
    assert "## 现在在做什么" not in prompt


def test_the_panel_gets_the_same_blocks_as_structured_data(client):
    """前端那一栏读的是同一份 ②③，只是给的是点得动的条目。

    提示词那一份 markdown 是给模型读的；这一份每条要带上自己的去处（话题 id）
    和一句话结论。人看总览时读到的东西，和芝士那一轮读到
    的是同一次取数（`TopicService.overview_auto_data`）——两个读者，一份来源。
    """
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)
    ended = client.post(
        "/topics",
        json={"project_id": project_id, "title": "做完的房间"},
        headers=session_auth_headers("user-1"),
    ).json()["data"]["id"]
    archived = client.post(
        f"/topics/{ended}/archive",
        json={"by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert archived.status_code == 200, archived.text

    body = client.get(f"/topics/{overview}/overview").json()["data"]
    assert body["root_topic_id"] == overview
    # 块按 ②③ 排，空块整块不出现（同提示词那一份）。
    assert [b["key"] for b in body["blocks"]] == ["active_topics", "closed_topics"]
    blocks = {b["key"]: b for b in body["blocks"]}
    assert [b["title"] for b in body["blocks"]] == ["现在在做什么", "已结束的话题"]

    # ② 活跃话题：去处是那个房间，状态是它最新的那张卡（还没开活）。
    (active,) = blocks["active_topics"]["items"]
    assert active["kind"] == "topic"
    assert active["topic_id"] == topic_id
    assert active["title"] == "干活的房间"
    assert active["status"] == "还没开活"
    assert active["owner"] is None

    # ③ 已结束的话题：归档的那一间落在这里，不在 ②。
    (closed,) = blocks["closed_topics"]["items"]
    assert closed["kind"] == "topic"
    assert closed["topic_id"] == ended
    assert closed["title"] == "做完的房间"


def test_only_the_overview_room_has_an_overview(client):
    """别的房间名下没有这么一件东西，是 404 而不是一个空壳。

    总览是项目级的：一个干活的房间读它自己的实况文档，没有人从那里看项目全局。
    给它拼一份出来，等于凭空多出一个「这个房间的总览」。
    """
    project_id, topic_id = _project_and_room(client)
    resp = client.get(f"/topics/{topic_id}/overview")
    assert resp.status_code == 404, resp.text


def test_a_stranger_cannot_read_the_overview(client):
    """权限和读总览文档一样：认得出来是谁，还得在名册上。"""
    project_id, _ = _project_and_room(client)
    overview = _overview_room(client, project_id)
    resp = client.get(
        f"/topics/{overview}/overview", headers=session_auth_headers("stranger")
    )
    assert resp.status_code == 403, resp.text
