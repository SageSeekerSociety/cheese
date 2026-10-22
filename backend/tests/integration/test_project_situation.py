"""项目总览上的「现状」，和让芝士起草项目目标。

现状：
- 材料只有点得开的记录：项目目标、近两周采纳的交付、还开着的任务。人在频道里说的
  话不进材料，也不让现状重写。
- 每块都有出处；出处对不上记录的那一块不上卡。
- 每块的状态看它引了什么：开着的任务是进行中，只有已采纳的交付是可用，只引了项目
  目标的是未开始。模型自己说的状态不算。
- 用项目自己的模型（分身默认模型）写，走项目在网关上的钥匙，花费照项目的结算。
- 有人打开、上次之后有新进展（如任务做完了）几分钟后就重写；近两周什么都没发生的
  项目不问模型。
- 现状人人看得到，所以私密频道里的任务不进它的材料。

起草：
- 只为还空着的项目目标起草；写过的项目目标由人和芝士一处一处地改。
- 替人回答问题的芝士只能把整篇写进空文档，写过的文档它整篇覆盖不了。
- 资料库里的 PDF，芝士读得到文字，不必在哪个频道里。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import fitz
import httpx

from app.core.config import settings
from app.core.sandbox_auth import mint_delegated_credential
from app.domain.agent.model_catalog import catalog
from app.domain.agent.supply import GATEWAY
from app.domain.project.models import Project
from app.domain.project_situation import service as situation_service
from app.domain.project_situation import writer
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic
from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)
from tests.support import library_uploads


def _project(client) -> str:
    return post_project(
        client, json={"name": "P"}, headers=session_auth_headers("alice")
    ).json()["data"]["id"]


def _channel(client, project: str, *, private: bool = False) -> str:
    r = client.post(
        "/topics",
        json={
            "project_id": project,
            "title": "私下" if private else "前端",
            **({"members_only": True} if private else {}),
        },
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _task(client, channel: str, title: str) -> str:
    async def seed() -> uuid.UUID:
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(channel))
            task = await TaskService(session).open_thread(
                project_id=room.project_id,
                room_id=room.id,
                title=title,
                owner_handle="alice",
                created_by="alice",
            )
            await session.commit()
            return task.id

    return str(client.portal.call(seed))


def _close(client, task: str) -> None:
    r = client.post(
        f"/topics/{task}/close",
        json={"conclusion": "做好了"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _accepted(client, channel: str, task: str, subject: str) -> str:
    """这个任务的一次交付被采纳了。"""

    async def seed() -> str:
        async with client.test_factory() as session:
            card = AcceptCard(
                topic_id=uuid.UUID(channel),
                task_id=uuid.UUID(task),
                reviewer_handle="alice",
                change_subject=subject,
                status=AcceptStatus.accepted,
                decided_by="alice",
                decided_at=datetime.now(UTC),
            )
            session.add(card)
            await session.commit()
            return str(card.id)

    return client.portal.call(seed)


def _goals(client, project: str, text: str) -> None:
    r = client.put(
        f"/documents/{_overview(client, project)}",
        json={"content": text, "expected_version": 0},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


class _Model:
    """在模型的位置上：记下每次交给它的材料，回事先给好的那几块。"""

    def __init__(self, parts: list | None = None) -> None:
        self.materials: list[str] = []
        self.parts = parts if parts is not None else []

    async def write(self, session, *, project_id, material, models, transport=None):
        self.materials.append(material)
        return writer.Written(answer={"parts": self.parts}, model="m")


class _Gateway:
    """项目在网关上的钥匙，和它花掉的钱记了几次。"""

    def __init__(self) -> None:
        self.charged = 0

    async def project_gateway_key(self, project_id):
        return "sk-project"

    async def charge_turn_spend(self, project_id, topic_id, turn_id):
        self.charged += 1


def _quiet_rewrites(monkeypatch) -> list:
    """后台那一步不自己跑：攒下来，由测试决定什么时候跑完。"""
    started: list = []

    def spawn(coro, *, name=None):
        started.append(coro)
        return True

    monkeypatch.setattr(situation_service, "background", SimpleNamespace(spawn=spawn))
    return started


def _read(client, project: str, *, at: datetime):
    async def go():
        async with client.test_factory() as session:
            return await situation_service.read(
                session, uuid.UUID(project), models=_Gateway(), now=at
            )

    return client.portal.call(go)


def _finish(client, started: list) -> None:
    while started:
        coro = started.pop(0)

        async def run(c=coro):
            await c

        client.portal.call(run)


def _card(client, project: str, started: list) -> list[dict]:
    """打开总览，等后台写完，再读一次：卡上的那几块。"""
    first = client.get(
        f"/projects/{project}/situation", headers=session_auth_headers("alice")
    )
    assert first.status_code == 200, first.text
    _finish(client, started)
    after = client.get(
        f"/projects/{project}/situation", headers=session_auth_headers("alice")
    ).json()["data"]
    assert after["writing"] is False
    return after["parts"]


def test_what_people_said_is_not_material(client, monkeypatch):
    model = _Model()
    monkeypatch.setattr(writer, "write", model.write)
    started = _quiet_rewrites(monkeypatch)
    project = _project(client)
    channel = _channel(client, project)
    post_message(client, channel, "alice", {"content": "状态灯的红灯判定仍在讨论"})
    _task(client, channel, "报名表单")
    _task(client, _channel(client, project, private=True), "只给几个人看的事")

    _card(client, project, started)

    assert "报名表单" in model.materials[0]
    assert "红灯判定" not in model.materials[0]
    assert "只给几个人看的事" not in model.materials[0]


def test_a_part_without_a_valid_source_is_dropped(client, monkeypatch):
    project = _project(client)
    _goals(client, project, "做一个活动报名小程序，支持候补。")
    channel = _channel(client, project)
    task = _task(client, channel, "报名表单")
    model = _Model(
        [
            {"name": "状态灯", "text": "红灯判定仍在讨论", "sources": []},
            {"name": "统计", "text": "按月导出", "sources": ["T9", "D4"]},
            {"name": "后台", "text": "订单查询", "sources": [], "goal": "后台管理"},
            {"name": "报名", "text": "报名表单正在补", "sources": ["T1", "T7"]},
        ]
    )
    monkeypatch.setattr(writer, "write", model.write)
    started = _quiet_rewrites(monkeypatch)

    parts = _card(client, project, started)

    assert [p["name"] for p in parts] == ["报名"]
    assert [(s["kind"], s["task_id"]) for s in parts[0]["sources"]] == [("task", task)]


def test_the_status_follows_the_records_not_the_model(client, monkeypatch):
    project = _project(client)
    _goals(client, project, "活动报名与候补；数据统计按月导出。")
    channel = _channel(client, project)
    signup = _task(client, channel, "报名与候补")
    card = _accepted(client, channel, signup, "支持候补自动递补")
    _close(client, signup)
    beta = _task(client, channel, "内测")
    first = _accepted(client, channel, beta, "接入数据迁移")
    # 模型自己说的状态和平台定的相反：卡上照平台定的。
    model = _Model(
        [
            {
                "name": "报名",
                "text": "候补自动递补",
                "sources": ["D2"],
                "status": "not_started",
            },
            {
                "name": "内测",
                "text": "数据迁移已接入",
                "sources": ["D1", "T1"],
                "status": "usable",
            },
            {
                "name": "统计",
                "text": "按月导出",
                "sources": [],
                "goal": "数据统计",
                "status": "usable",
            },
        ]
    )
    monkeypatch.setattr(writer, "write", model.write)
    started = _quiet_rewrites(monkeypatch)

    parts = _card(client, project, started)

    assert [(p["name"], p["status"]) for p in parts] == [
        ("报名", "usable"),
        ("内测", "underway"),
        ("统计", "not_started"),
    ]
    assert [(s["kind"], s["card_id"]) for s in parts[0]["sources"]] == [
        ("delivery", card)
    ]
    assert {s["card_id"] or s["task_id"] for s in parts[1]["sources"]} == {first, beta}
    assert parts[2]["sources"] == [
        {
            "kind": "goal",
            "card_id": None,
            "task_id": None,
            "room_id": None,
            "title": "",
            "quote": "数据统计",
        }
    ]


def test_talk_does_not_rewrite_and_a_finished_task_does_in_minutes(client, monkeypatch):
    model = _Model()
    monkeypatch.setattr(writer, "write", model.write)
    started = _quiet_rewrites(monkeypatch)
    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel, "报名表单")
    now = datetime.now(UTC)
    _read(client, project, at=now)
    _finish(client, started)
    assert len(model.materials) == 1

    # 只有人说话：多久都不重写。
    post_message(client, channel, "alice", {"content": "接着做"})
    assert _read(client, project, at=now + timedelta(hours=3)).writing is False

    # 任务做完了：几分钟后就重写。
    _close(client, task)
    assert _read(client, project, at=now + timedelta(minutes=2)).writing is False
    assert _read(client, project, at=now + timedelta(hours=3)).writing is True


def test_a_project_where_nothing_happened_asks_no_model(client, monkeypatch):
    model = _Model()
    monkeypatch.setattr(writer, "write", model.write)
    started = _quiet_rewrites(monkeypatch)
    project = _project(client)
    post_message(client, _channel(client, project), "alice", {"content": "开工"})

    parts = _card(client, project, started)

    assert model.materials == []
    assert parts == []


def test_the_situation_is_written_by_the_projects_own_model_on_its_key(
    client, monkeypatch
):
    project = _project(client)

    async def choose() -> str:
        async with client.test_factory() as session:
            row = await session.get(Project, uuid.UUID(project))
            ids = [
                i for i, c in catalog(row.settings).items() if c["supply"] == GATEWAY
            ]
            row.settings = {**(row.settings or {}), "default_subagent_model": ids[-1]}
            await session.commit()
            return ids[-1]

    chosen = client.portal.call(choose)
    sent: list[httpx.Request] = []
    answer = {"parts": [{"name": "报名", "text": "可报名", "sources": ["D1"]}]}

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(answer)}}]},
        )

    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway.test")
    gateway = _Gateway()

    async def go():
        async with client.test_factory() as session:
            return await writer.write(
                session,
                project_id=uuid.UUID(project),
                material="材料",
                models=gateway,
                transport=httpx.MockTransport(reply),
            )

    written = client.portal.call(go)

    assert written is not None and written.model == chosen
    assert written.answer == answer
    assert json.loads(sent[0].content)["model"] == chosen
    assert sent[0].headers["authorization"] == "Bearer sk-project"
    assert gateway.charged == 1


def _overview(client, project: str) -> str:
    r = client.get(
        f"/projects/{project}/overview", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _answering(project: str, *, agent: str = "cheese") -> dict:
    token = mint_delegated_credential(
        user_id=None,
        handle="alice",
        agent=agent,
        project_id=project,
        topic_id=None,
        work=str(uuid.uuid4()),
        read_only=False,
        ttl_s=300,
    )
    return {"X-Cheese-Token": token}


def test_an_answering_agent_writes_only_a_blank_document_whole(client):
    project = _project(client)
    overview = _overview(client, project)

    first = client.put(
        f"/documents/{overview}",
        json={"content": "为课程做一个预约小程序。", "expected_version": 0},
        headers=_answering(project),
    )
    assert first.status_code == 200, first.text
    version = client.get(
        f"/documents/{overview}", headers=session_auth_headers("alice")
    ).json()["data"]["doc_version"]

    over = client.put(
        f"/documents/{overview}",
        json={"content": "整篇换掉。", "expected_version": version},
        headers=_answering(project),
    )
    assert over.status_code == 403, over.text
    kept = client.get(
        f"/documents/{overview}", headers=session_auth_headers("alice")
    ).json()["data"]["content"]
    assert "预约小程序" in kept


def test_drafting_is_only_for_a_blank_project_overview(client):
    project = _project(client)
    overview = _overview(client, project)
    other = client.post(
        f"/projects/{project}/documents",
        json={"title": "周报"},
        headers=session_auth_headers("alice"),
    )
    assert other.status_code == 200, other.text

    on_other = client.post(
        f"/documents/{other.json()['data']['id']}/agent",
        json={"preset": "draft"},
        headers=session_auth_headers("alice"),
    )
    written = client.put(
        f"/documents/{overview}",
        json={"content": "已经写好的目标。", "expected_version": 0},
        headers=session_auth_headers("alice"),
    )
    assert written.status_code == 200, written.text
    on_written = client.post(
        f"/documents/{overview}/agent",
        json={"preset": "draft"},
        headers=session_auth_headers("alice"),
    )

    assert on_other.status_code == 422, on_other.text
    assert on_written.status_code == 422, on_written.text


def _pdf(text: str) -> bytes:
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), text)
    return pdf.tobytes()


def test_a_library_pdf_reads_as_text_outside_any_channel(client):
    project = _project(client)
    r = library_uploads.put_in(
        client,
        project,
        "要求.pdf",
        _pdf("Final report due December 22"),
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    read = client.get(
        f"/projects/{project}/library/text",
        params={"path": "library/要求.pdf"},
        headers=_answering(project),
    )

    assert read.status_code == 200, read.text
    assert "Final report due December 22" in read.json()["data"]["content"]
