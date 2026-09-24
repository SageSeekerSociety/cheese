"""模型绑在活上：一轮怎么解析，卡上怎么显示。

不变量 I17 的 ②③ 在这里（④ 在 `unit/test_device_provider.py`）：

  ② 房间主线的模型在一轮里改不动
  ③ 卡上显示的模型 = 这条活 `usage` 里最后一行的 `model`，而 `tasks` 上没有
     任何一列存「显示什么」

**① 「改一条活的绑定，下一轮生效」今天没有落点，本 PR 不声称验过它。**「这条活的
绑定」要生效，得有一个「派这条活」的时刻去读它，而平台今天根本没有派活的路径：
一条活是房间会话里的一个子 agent，由 agent 自己起。那个时刻在 P33（骨架的子
agent 四条硬性要求，依赖本条）出生，读侧跟着它一起落。写侧同理 —— 今天没有任何
接口或界面能改一条活的绑定，下面的 `_rebind` 直接改 ORM 对象。所以这里验的是
**读**这一级：绑定是读的时候解析的，没有一处把它写死。
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.agent.chat import ChatService
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent_instance.models import AgentInstance
from app.domain.project.models import Project
from app.domain.project.services import ProjectService
from app.domain.room_task.models import Task
from app.domain.topic.services import TopicService
from app.domain.usage.models import ResourceUsage
from tests.conftest import stub_compute
from tests.integration.conftest import registered
from tests.unit.test_device_provider import FakeHub


@pytest.fixture(autouse=True)
def configured_default(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "deepseek-flash")
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


def _on_a_machine() -> ClaudeCodeRuntime:
    """机器在别处，所以启动环境在机器那边组 —— 这一支只答「用哪个模型」。"""
    return ClaudeCodeRuntime(DeviceChannel())


async def _room(factory) -> dict[str, uuid.UUID]:
    """一个房间，里面一条活。项目按正常路子建，所以它自带它的芝士。"""
    async with factory() as session:
        await registered(session, "alice")
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


def _chat(factory, tmp_path) -> ChatService:
    return ChatService(
        session_factory=factory,
        compute=stub_compute(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


async def _rebind(factory, ids, model: str) -> None:
    async with factory() as session:
        work = await session.get(Task, ids["work"])
        work.model = model
        await session.commit()


async def _use_the_pool(factory, ids) -> None:
    """把这个项目挪到网关池 —— 供给是项目自己的设置，不是部署的开关。"""
    async with factory() as session:
        project = await session.get(Project, ids["project"])
        project.settings = {**(project.settings or {}), "supply": "gateway"}
        await session.commit()


# —— ② 房间主线的模型在一轮里改不动 ————————————————————————————————


@pytest.mark.anyio
async def test_the_rooms_main_line_runs_on_the_project_default(client, tmp_path):
    ids = client.portal.call(lambda: _room(client.test_request_factory))

    kwargs, _route = client.portal.call(
        lambda: _chat(client.test_request_factory, tmp_path)._model_kwargs(
            ids["project"], _on_a_machine(), ids["room"]
        )
    )

    assert kwargs["model"] == "deepseek-flash"


@pytest.mark.anyio
async def test_editing_the_agent_changes_the_next_turn_not_the_prepared_turn(
    client, tmp_path
):
    """A teammate override changes its next turn without mutating a prepared turn."""
    ids = client.portal.call(lambda: _room(client.test_request_factory))
    chat = _chat(client.test_request_factory, tmp_path)

    in_flight, _route = client.portal.call(
        lambda: chat._model_kwargs(ids["project"], _on_a_machine(), ids["room"])
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
        instance.configuration = {**instance.configuration, "model": "sonnet"}
        await session.commit()

    later, _route = client.portal.call(
        lambda: chat._model_kwargs(ids["project"], _on_a_machine(), ids["room"])
    )

    assert in_flight["model"] == "deepseek-flash"
    assert later["model"] == "claude-sonnet-5"


@pytest.mark.anyio
async def test_changing_the_projects_default_model_retires_the_running_screen(
    client, tmp_path, monkeypatch
):
    """把项目默认模型换掉，房间那块正在跑的屏幕在下一个 task boundary 被收掉。

    一块屏幕是一个已经起好的 `claude` 进程，出生那一刻钉死的东西它一样也换不掉。
    唯一比较「这块屏幕还配不配得上现在的选择」的地方是 `CHEESE_AGENT_CONFIG` 这个
    哈希（`device_provider._ensure_screen`），所以选择里有什么，就得哈希什么。

    模型从前住在 agent 的 configuration 里，跟着那个 dict 一起被哈希；它搬到项目
    设置上之后，不显式放进来就漏了。漏掉的样子是：屏幕带着旧的系统提示词和旧的
    缓存继续跑，而准入已经按新绑定解析每一个请求。

    换的只有模型，池没动：这样这条测试断的就只是「模型在不在哈希里」。池本来就
    另有一处进哈希（订阅形状才加的原生 RC 参数），拿换池来测会被那一处兜住。
    """
    from app.core.config import settings as app_settings

    ca = tmp_path / "proxy-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(app_settings, "subscription_ca_backend_path", str(ca))
    monkeypatch.setattr(app_settings, "agent_model", "glm-5.2")
    ids = client.portal.call(lambda: _room(client.test_request_factory))
    client.portal.call(lambda: _use_the_pool(client.test_request_factory, ids))
    chat = _chat(client.test_request_factory, tmp_path)
    hub = FakeHub()
    provider = DeviceChannel(hub=hub, public_base="http://cheese.test")

    async def screen_for(kwargs):
        # 只带这一个键：环境准备那一支会去问机器状态，而这里没有真机器，也不是这
        # 条测试要问的事 —— 要问的是这个哈希变没变，以及变了之后屏幕怎么办。
        return await provider._ensure_screen(
            device_id="dev1",
            agent_user_id=1,
            agent_handle="cheese",
            project_id=ids["project"],
            topic_id=ids["room"],
            token="tok",
            env={"CHEESE_AGENT_CONFIG": kwargs["env"]["CHEESE_AGENT_CONFIG"]},
            launch=ClaudeLaunch(system_prompt="", model=kwargs["model"]),
        )

    before, before_pool = client.portal.call(
        lambda: chat._model_kwargs(ids["project"], _on_a_machine(), ids["room"])
    )
    running = client.portal.call(lambda: screen_for(before))
    assert client.portal.call(lambda: screen_for(before)) is running, (
        "什么都没改，屏幕当然接着用"
    )

    monkeypatch.setattr(app_settings, "agent_model", "deepseek-flash")
    after, after_pool = client.portal.call(
        lambda: chat._model_kwargs(ids["project"], _on_a_machine(), ids["room"])
    )
    assert (before["model"], after["model"]) == ("glm-5.2", "deepseek-flash")
    assert before_pool == after_pool, "池没动，动的只有模型"

    restarted = client.portal.call(lambda: screen_for(after))
    assert restarted.sid != running.sid
    assert hub.closed == [running.sid]


# —— 绑定是读的时候解析的，没有一处把它写死 ————————————————————


@pytest.mark.anyio
async def test_rebinding_a_work_shows_on_the_next_read_of_its_card(client, tmp_path):
    """改一条活的绑定，下一次读它的卡就换了 —— 没有一处把解析结果写下来。

    而房间主线已经开跑的那一轮拿的是开轮那一刻的快照，改不动。
    """
    ids = client.portal.call(lambda: _room(client.test_request_factory))
    in_flight, _route = client.portal.call(
        lambda: _chat(client.test_request_factory, tmp_path)._model_kwargs(
            ids["project"], _on_a_machine(), ids["room"]
        )
    )

    assert _card(client, ids)["model"] == "deepseek-flash"

    client.portal.call(lambda: _rebind(client.test_request_factory, ids, "opus"))

    assert _card(client, ids)["model"] == "opus"
    assert in_flight["model"] == "deepseek-flash"


@pytest.mark.anyio
async def test_one_broken_binding_does_not_take_the_rooms_whole_board_down(client):
    """一条活绑了本项目用不了的模型，卡上照原样写出那个名字，整屏照常读得出来。

    在渲染里拒绝，坏掉的不是那一张卡，是这个房间的所有卡一起 422 —— 而这一屏正是
    唯一能看见、进而改掉这条绑定的地方，等于把出口一起关上。触发它不需要有人手写
    数据库：项目把供给从订阅改成网关，或者运维从目录里摘掉一个型号，先前绑上去的
    那批活立刻全部解析不出来。拒绝留在执行路径上（`binding.resolve`，I27）。
    """
    ids = client.portal.call(lambda: _room(client.test_request_factory))
    async with client.test_factory() as session:
        session.add(
            Task(project_id=ids["project"], room_id=ids["room"], title="另一条活")
        )
        await session.commit()
    client.portal.call(
        lambda: _rebind(client.test_request_factory, ids, "no-such-model")
    )

    board = client.get(f"/topics/{ids['room']}/tasks")
    assert board.status_code == 200, board.text
    shown = {item["title"]: item["model"] for item in board.json()["data"]["data"]}
    assert shown == {"一条活": "no-such-model", "另一条活": "deepseek-flash"}

    assert _card(client, ids)["model"] == "no-such-model"


# —— ③ 卡上的模型从用量算，不存状态列 ————————————————————————————


@pytest.mark.anyio
async def test_the_card_shows_the_last_model_the_work_actually_spent_on(client):
    ids = client.portal.call(lambda: _room(client.test_request_factory))
    client.portal.call(lambda: _rebind(client.test_request_factory, ids, "opus"))

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
    # 写的是目录里的 id：钱花在 `claude-sonnet-5` 上，卡上仍然是 `sonnet`。
    assert _card(client, ids)["model"] == "sonnet"

    async with client.test_factory() as session:
        work = await session.get(Task, ids["work"])
        stored = {
            column.name: getattr(work, column.name) for column in Task.__table__.columns
        }

    # 守卫：显示出来的那个值，在 `tasks` 的任何一列里都找不到。一列存「显示什么」
    # 就是第二份声明，它和账单对不上的那天没人说得清是谁写错的。
    assert "sonnet" not in stored.values()
    assert stored["model"] == "opus"


@pytest.mark.anyio
async def test_spending_does_not_change_the_word_the_card_uses_for_one_model(client):
    """同一个模型，花钱前后卡上是同一个字。

    绑定说的是目录里的 id（`sonnet`），用量行记的是真发出去的名字
    （`claude-sonnet-5`）。两边各吐各的，这张卡就会在第一次请求之后自己换一个名
    字，而模型根本没动 —— 用户读到的是「模型被换了」。③ 的字面（显示 = 最后一行
    用量的 model）两种写法都满足，所以只有这一条能把它钉住。
    """
    ids = client.portal.call(lambda: _room(client.test_request_factory))
    client.portal.call(lambda: _rebind(client.test_request_factory, ids, "sonnet"))
    before = _card(client, ids)["model"]

    async with client.test_factory() as session:
        session.add(
            ResourceUsage(
                project_id=ids["project"],
                topic_id=ids["room"],
                task_id=ids["work"],
                model="claude-sonnet-5",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )
        await session.commit()

    assert before == "sonnet"
    assert _card(client, ids)["model"] == before
