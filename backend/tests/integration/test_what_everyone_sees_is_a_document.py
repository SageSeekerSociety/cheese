"""没有项目记忆池：所有人都该看见的东西是一份文档（结论 7）。

一个共享池和一份文档差的不是存在哪儿。池子没有主、不留痕、人翻不到也改不了，
而它偏偏又是「大家都该知道的事」唯一的落点——于是项目的共识住在一个只有模型读
得到的地方。文档三样都有，所以那一路的去向是项目总览那个房间的实况文档。

这一组守两头：写进去的那一条，人在总览文档里读得到；别的房间跑一轮时，总览文档
整份在它的提示词里——共享池能做到的第二件事，文档也做到了。
"""

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import redundant
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import memory_store
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.integration.conftest import chat_ws_url

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
    project_id = client.post(
        "/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "干活的房间", "created_by": "user-1"},
    ).json()["data"]["id"]
    return project_id, topic_id


def _overview_room(client, project_id: str) -> str:
    return client.get(f"/projects/{project_id}").json()["data"]["root_topic_id"]


def _doc_text(client, topic_id: str) -> str:
    doc = client.get(f"/topics/{topic_id}/doc").json()["data"]
    return (doc or {}).get("content", "")


def _write_for_everyone(client, project_id: str, topic_id: str, fact: str) -> None:
    r = client.post(
        f"/projects/{project_id}/memory",
        json={"content": fact, "topic": topic_id, "scope": "everyone"},
    )
    assert r.status_code == 200, r.text


def _project_agent(client, project_id: str) -> str:
    return client.get(f"/projects/{project_id}/agents").json()["data"]["data"][0][
        "handle"
    ]


def _checkout_holding(monkeypatch, *lines: dict) -> None:
    async def search(terms: list[str]) -> list[dict]:
        return [
            line
            for line in lines
            if any(term.lower() in str(line["text"]).lower() for term in terms)
        ]

    monkeypatch.setattr(
        redundant,
        "agent_checkout_search",
        lambda db, room, agent_handle, harness: search,
    )


def test_a_fact_for_everyone_is_readable_in_the_project_overview_document(client):
    """芝士 写下一条「所有人都该知道」的事实，人打开总览文档就看得到。"""
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)

    _write_for_everyone(client, project_id, topic_id, FACT)

    assert FACT in _doc_text(client, overview)


def test_it_is_a_document_and_not_a_memory(client):
    """同一条事实不会同时又是一条记忆。

    两份的那一刻起，改文档的人和整理记忆的芝士各改各的，而谁也不知道另一份还在。
    """
    project_id, topic_id = _project_and_room(client)
    _write_for_everyone(client, project_id, topic_id, FACT)

    hits = client.post(
        f"/projects/{project_id}/memory/search",
        json={"query": "中期答辩 demo", "topic": topic_id},
    ).json()["data"]["hits"]
    assert hits == []
    assert client.get(f"/memory?project_id={project_id}").json()["data"]["data"] == []


def test_the_overview_document_keeps_what_was_already_written_in_it(client):
    """追加，不改写：人写在总览文档里的字，芝士添一条观察时一个字都不动。"""
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)
    client.put(
        f"/topics/{overview}/doc",
        json={
            "content": "## 目标\n做课程推荐系统",
            "author": "user-1",
            "expected_version": 0,
        },
    )

    _write_for_everyone(client, project_id, topic_id, FACT)

    text = _doc_text(client, overview)
    assert "做课程推荐系统" in text
    assert FACT in text


def test_another_room_reads_the_overview_document_on_its_next_turn(client, stub_hooks):
    """别的房间跑一轮，总览那一份整份在提示词里——而它自己的文档是另一份。"""
    project_id, topic_id = _project_and_room(client)
    _write_for_everyone(client, project_id, topic_id, FACT)

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 现在什么状态"})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break

    prompt = stub_hooks.last_system_prompt
    assert prompt is not None
    assert FACT in prompt


def test_every_fact_lands_in_one_section_and_carries_who_wrote_it(client):
    """文档里看得见是谁说的，而且这几条聚在自己那一节里。

    裸追加到末尾的一条，落在文档最后一个标题底下——总览文档最后一节叫「数据」，
    读的人就把它当成数据那一节的内容；而正文里不署名，谁说的就只剩「最近一次编辑
    这份文档的人」，下一个人一改就没了。
    """
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)
    agent = _project_agent(client, project_id)
    client.put(
        f"/topics/{overview}/doc",
        json={
            "content": "## 目标\n做课程推荐系统\n\n## 数据\n教务处脱敏导出",
            "author": "user-1",
            "expected_version": 0,
        },
    )

    _write_for_everyone(client, project_id, topic_id, FACT)
    _write_for_everyone(client, project_id, topic_id, "前端交给张衡，后端交给李四")

    text = _doc_text(client, overview)
    for fact in (FACT, "前端交给张衡，后端交给李四"):
        assert f"{fact} —— @{agent}" in text
    # 两条在同一节里往下排，不是一条一个标题——被观察切碎的文档没人再往里写字。
    section = [line for line in text.split("\n") if line.startswith("## ")]
    assert len(section) == len(set(section)) == 3
    # 人自己写的那一节到此为止，芝士 的观察不挂在它底下。
    assert text.index("教务处脱敏导出") < text.index(FACT)


def test_a_fact_the_repo_already_carries_still_goes_into_the_document(
    client, monkeypatch
):
    """repo 里写着，不是不让所有人知道的理由。

    「只记 repo 里查不到的」是记忆那一侧的判据（结论 61）——记忆是一份会过期的副
    本。文档不是副本，它是人和所有芝士共看的那一份状态，而项目定了什么、谁负责什
    么写在哪个文件里，正是它该说的话。两条一起测：同一条事实，记忆那一路拒，文档
    这一路收。
    """
    project_id, topic_id = _project_and_room(client)
    overview = _overview_room(client, project_id)
    fact = "前端构建用 pnpm，不要用 npm"
    _checkout_holding(
        monkeypatch,
        {
            "path": "docs/frontend.md",
            "line": 12,
            "text": "前端构建用 pnpm，不要用 npm。",
        },
    )

    refused = client.post(
        f"/projects/{project_id}/memory",
        json={"content": fact, "topic": topic_id},
    )
    assert refused.status_code == 422

    _write_for_everyone(client, project_id, topic_id, fact)
    assert fact in _doc_text(client, overview)


def _move_statement() -> str:
    """迁移真正会跑的那一句，从迁移模块里取，不照抄一份。"""
    spec = importlib.util.spec_from_file_location("_p36_move", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.APPEND_PROJECT_MEMORY_TO_OVERVIEW


def test_the_migration_lands_every_project_pool_row_in_that_document(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """存量：``scope='project'`` 的每一条，迁移跑完都在目标文档里找得到同一份内容。

    连跑两遍——dev 先跑迁移后换容器，窗口里旧镜像还在往这个池写，这一条要由 P36b
    原样再跑一遍，所以第二遍不能把同一条再追加一次。行只搬不删：这批行不可再生
    （结论 61），文档写错了没有第二份可对照。
    """

    async def run() -> None:
        project = await ProjectService(db_session).create(
            name="有总览文档的项目", owner_handle="andyl", forge_kind="github_app"
        )
        assert project.root_topic_id is not None
        await TopicService(db_session).edit_doc(
            topic_id=project.root_topic_id,
            content="## 目标\n做课程推荐系统",
            author="andyl",
            expected_version=0,
        )
        store = memory_store(db_session)
        facts = ["数据来源是教务处脱敏数据", FACT]
        for fact in facts:
            await store.remember(MemoryScope.project, str(project.id), fact)
        await db_session.flush()

        for _ in range(2):
            await db_session.execute(sa.text(_move_statement()))
            await db_session.flush()

            doc = await TopicService(db_session).get_doc(project.root_topic_id)
            assert doc is not None
            for fact in facts:
                assert doc.content.count(fact) == 1
            # 人自己写的那一段还在，被搬进来的那几条排在它后面。
            assert "做课程推荐系统" in doc.content
            # 只搬不删：窗口里旧镜像还在写这个池，删了就是孤儿。
            assert sorted(
                await store.recall(MemoryScope.project, str(project.id))
            ) == sorted(facts)

    _portal.call(run)


def test_the_migration_leaves_a_project_without_an_overview_document_alone(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """总览房间还没有文档的项目，这一步不建一份。

    建出来的那一份内容全是记忆条目，文档栏第一次被打开就是一张清单——而这个房间
    从来没有人写过文档。那批行留在表里，由 P36b 接着处理。
    """

    async def run() -> None:
        project = await ProjectService(db_session).create(
            name="没有总览文档的项目", owner_handle="andyl", forge_kind="github_app"
        )
        store = memory_store(db_session)
        await store.remember(MemoryScope.project, str(project.id), FACT)
        await db_session.flush()

        await db_session.execute(sa.text(_move_statement()))
        await db_session.flush()

        assert project.root_topic_id is not None
        assert await TopicService(db_session).get_doc(project.root_topic_id) is None
        assert await store.recall(MemoryScope.project, str(project.id)) == [FACT]

    _portal.call(run)


def test_the_overview_room_does_not_read_its_own_document_twice(client, stub_hooks):
    """总览房间自己那一轮，这份文档只出现一次。

    同一份状态在提示词里出现两遍，模型会把它当成两件事——两份还可能一新一旧。
    """
    project_id, _ = _project_and_room(client)
    overview = _overview_room(client, project_id)
    client.put(
        f"/topics/{overview}/doc",
        json={"content": FACT, "author": "user-1", "expected_version": 0},
    )

    with client.websocket_connect(chat_ws_url(overview, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 现在什么状态"})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break

    prompt = stub_hooks.last_system_prompt
    assert prompt is not None
    assert prompt.count(FACT) == 1
