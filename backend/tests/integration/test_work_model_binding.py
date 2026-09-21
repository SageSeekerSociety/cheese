"""模型绑在活上：一轮怎么解析，卡上怎么显示。

这里是不变量 I17 的 ①②③ 三条（④ 在 `unit/test_device_provider.py`）：

  ① 改一条活的绑定，下一轮生效而当前轮不变
  ② 房间主线的模型在一轮里改不动
  ③ 卡上显示的模型 = 这条活 `usage` 里最后一行的 `model`，而 `tasks` 上没有
     任何一列存「显示什么」
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
from app.domain.agent_instance.models import AgentInstance
from app.domain.project.services import ProjectService
from app.domain.room_task.models import Task
from app.domain.topic.services import TopicService
from app.domain.usage.models import ResourceUsage
from tests.conftest import stub_compute


def _on_a_machine() -> ClaudeCodeRuntime:
    """机器在别处，所以启动环境在机器那边组 —— 这一支只答「用哪个模型」。"""
    return ClaudeCodeRuntime(DeviceChannel())


async def _room(client) -> dict[str, uuid.UUID]:
    """一个房间，里面一条活。项目按正常路子建，所以它自带它的芝士。"""
    async with client.test_factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        room = await TopicService(session).create(
            project_id=project.id, title="房间", created_by="alice"
        )
        work = Task(project_id=project.id, room_id=room.id, title="一条活")
        session.add(work)
        await session.flush()
        ids = {"project": project.id, "room": room.id, "work": work.id}
        await session.commit()
    return ids


def _card(client, ids) -> dict:
    response = client.get(f"/topics/{ids['room']}/tasks/{ids['work']}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _chat(client, tmp_path) -> ChatService:
    return ChatService(
        session_factory=client.test_factory,
        compute=stub_compute(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


async def _rebind(client, ids, model: str) -> None:
    async with client.test_factory() as session:
        work = await session.get(Task, ids["work"])
        work.model = model
        await session.commit()


@pytest.fixture
def subscribed(monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)


# —— ② 房间主线的模型在一轮里改不动 ————————————————————————————————


@pytest.mark.anyio
async def test_the_rooms_main_line_runs_on_the_project_default(
    client, tmp_path, subscribed
):
    ids = await _room(client)

    kwargs, _route = await _chat(client, tmp_path)._model_kwargs(
        ids["project"], _on_a_machine(), ids["room"]
    )

    assert kwargs["model"] == "claude-sonnet-5"


@pytest.mark.anyio
async def test_editing_the_agent_does_not_move_the_rooms_main_line(
    client, tmp_path, subscribed
):
    """模型不是参与者的属性（结论 3、44）。把这个 agent 存着的模型改掉，房间主线
    照旧走项目默认 —— 「想换模型就再建一个 agent」正是这次拆掉的形状。

    这同时是「一轮里改不动」：一轮的模型是开轮那一刻取的快照，之后改什么都不会
    回头改它。
    """
    ids = await _room(client)
    chat = _chat(client, tmp_path)

    in_flight, _route = await chat._model_kwargs(
        ids["project"], _on_a_machine(), ids["room"]
    )

    async with client.test_factory() as session:
        instance = (
            (
                await session.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == ids["project"]
                    )
                )
            )
            .scalars()
            .first()
        )
        assert instance is not None, "项目应当自带它的芝士"
        instance.configuration = {**instance.configuration, "model": "glm-5.2"}
        await session.commit()

    later, _route = await chat._model_kwargs(
        ids["project"], _on_a_machine(), ids["room"]
    )

    assert in_flight["model"] == "claude-sonnet-5"
    assert later["model"] == in_flight["model"]


# —— ① 改一条活的绑定，下一轮生效而当前轮不变 ————————————————————


@pytest.mark.anyio
async def test_rebinding_a_work_reaches_the_next_read_not_the_turn_in_flight(
    client, tmp_path, subscribed
):
    """绑定是**读的时候**解析的，没有一处把它写死。

    所以改一条活的绑定，下一次读它就换了；而已经开跑的那一轮拿的是开轮那一刻的
    快照，改不动。
    """
    ids = await _room(client)
    in_flight, _route = await _chat(client, tmp_path)._model_kwargs(
        ids["project"], _on_a_machine(), ids["room"]
    )

    assert _card(client, ids)["model"] == "sonnet"

    await _rebind(client, ids, "opus")

    assert _card(client, ids)["model"] == "opus"
    assert in_flight["model"] == "claude-sonnet-5"


@pytest.mark.anyio
async def test_a_work_bound_to_a_model_the_project_cannot_use_is_refused(
    client, subscribed
):
    """答不出就拒绝并说出来，不悄悄退回项目默认（I27）。"""
    ids = await _room(client)
    await _rebind(client, ids, "no-such-model")

    response = client.get(f"/topics/{ids['room']}/tasks/{ids['work']}")
    assert response.status_code == 422, response.text
    assert "no-such-model" in response.text


# —— ③ 卡上的模型从用量算，不存状态列 ————————————————————————————


@pytest.mark.anyio
async def test_the_card_shows_the_last_model_the_work_actually_spent_on(
    client, subscribed
):
    ids = await _room(client)
    await _rebind(client, ids, "opus")

    async with client.test_factory() as session:
        for index, model in enumerate(("claude-opus-5", "claude-sonnet-5")):
            session.add(
                ResourceUsage(
                    project_id=ids["project"],
                    topic_id=ids["room"],
                    task_id=ids["work"],
                    model=model,
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    created_at=datetime(2026, 9, 1, 1 + index, tzinfo=UTC),
                )
            )
        await session.commit()

    # 它真花的最后一个，不是它绑的那个 —— 绑定说的是下一轮，用量说的是已经发生的。
    assert _card(client, ids)["model"] == "claude-sonnet-5"

    async with client.test_factory() as session:
        work = await session.get(Task, ids["work"])
        stored = {
            column.name: getattr(work, column.name) for column in Task.__table__.columns
        }

    # 守卫：显示出来的那个值，在 `tasks` 的任何一列里都找不到。一列存「显示什么」
    # 就是第二份声明，它和账单对不上的那天没人说得清是谁写错的。
    assert "claude-sonnet-5" not in stored.values()
    assert stored["model"] == "opus"
