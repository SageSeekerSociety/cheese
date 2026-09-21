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
from tests.unit.test_device_provider import ReuseGateHub


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


async def _use_the_pool(client, ids) -> None:
    """把这个项目挪到网关池 —— 供给是项目自己的设置，不是部署的开关。"""
    async with client.test_factory() as session:
        project = await session.get(Project, ids["project"])
        project.settings = {**(project.settings or {}), "supply": "gateway"}
        await session.commit()


# —— ② 房间主线的模型在一轮里改不动 ————————————————————————————————


@pytest.mark.anyio
async def test_the_rooms_main_line_runs_on_the_project_default(client, tmp_path):
    ids = await _room(client)

    kwargs, _route = await _chat(client, tmp_path)._model_kwargs(
        ids["project"], _on_a_machine(), ids["room"]
    )

    assert kwargs["model"] == "claude-sonnet-5"


@pytest.mark.anyio
async def test_editing_the_agent_does_not_move_the_rooms_main_line(client, tmp_path):
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


@pytest.mark.anyio
async def test_changing_the_projects_default_model_retires_the_running_screen(
    client, tmp_path, monkeypatch
):
    """把项目默认模型换掉，房间那块正在跑的屏幕在下一个 task boundary 被收掉。

    一块屏幕是一个已经起好的 `claude` 进程：`--model` 在它的 argv 里，三个 family
    别名在它的启动环境里，两样都是出生那一刻钉死的，而没有任何代码比 argv。唯一
    比较「这块屏幕还配不配得上现在的选择」的地方是 `CHEESE_AGENT_CONFIG` 这个哈希
    （`device_provider._ensure_screen`），所以选择里有什么，就得哈希什么。

    模型从前住在 agent 的 configuration 里，跟着那个 dict 一起被哈希；它搬到项目
    设置上之后，不显式放进来就漏了。漏掉的样子是：屏幕带着旧的 `--model` 和三个
    旧别名继续跑，而准入已经按新绑定解析每一个请求 —— 这个房间此后每一轮都死在
    「LiteLLM 收到它不认识的名字」上，直到有人手动重启屏幕。

    换的只有模型，池没动：这样这条测试断的就只是「模型在不在哈希里」。池本来就
    另有一处进哈希（订阅形状才加的原生 RC 参数），拿换池来测会被那一处兜住。
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "agent_model", "glm-5.2")
    ids = await _room(client)
    await _use_the_pool(client, ids)
    chat = _chat(client, tmp_path)
    hub = ReuseGateHub()
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

    before, before_pool = await chat._model_kwargs(
        ids["project"], _on_a_machine(), ids["room"]
    )
    running = await screen_for(before)
    assert await screen_for(before) is running, "什么都没改，屏幕当然接着用"

    monkeypatch.setattr(app_settings, "agent_model", "deepseek-flash")
    after, after_pool = await chat._model_kwargs(
        ids["project"], _on_a_machine(), ids["room"]
    )
    assert (before["model"], after["model"]) == ("glm-5.2", "deepseek-flash")
    assert before_pool == after_pool, "池没动，动的只有模型"

    restarted = await screen_for(after)
    assert restarted.sid != running.sid
    assert hub.closed == [running.sid]


# —— 绑定是读的时候解析的，没有一处把它写死 ————————————————————


@pytest.mark.anyio
async def test_rebinding_a_work_shows_on_the_next_read_of_its_card(client, tmp_path):
    """改一条活的绑定，下一次读它的卡就换了 —— 没有一处把解析结果写下来。

    而房间主线已经开跑的那一轮拿的是开轮那一刻的快照，改不动。
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
async def test_one_broken_binding_does_not_take_the_rooms_whole_board_down(client):
    """一条活绑了本项目用不了的模型，卡上照原样写出那个名字，整屏照常读得出来。

    在渲染里拒绝，坏掉的不是那一张卡，是这个房间的所有卡一起 422 —— 而这一屏正是
    唯一能看见、进而改掉这条绑定的地方，等于把出口一起关上。触发它不需要有人手写
    数据库：项目把供给从订阅改成网关，或者运维从目录里摘掉一个型号，先前绑上去的
    那批活立刻全部解析不出来。拒绝留在执行路径上（`binding.resolve`，I27）。
    """
    ids = await _room(client)
    async with client.test_factory() as session:
        session.add(
            Task(project_id=ids["project"], room_id=ids["room"], title="另一条活")
        )
        await session.commit()
    await _rebind(client, ids, "no-such-model")

    board = client.get(f"/topics/{ids['room']}/tasks")
    assert board.status_code == 200, board.text
    shown = {item["title"]: item["model"] for item in board.json()["data"]["data"]}
    assert shown == {"一条活": "no-such-model", "另一条活": "sonnet"}

    assert _card(client, ids)["model"] == "no-such-model"


# —— ③ 卡上的模型从用量算，不存状态列 ————————————————————————————


@pytest.mark.anyio
async def test_the_card_shows_the_last_model_the_work_actually_spent_on(client):
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
    ids = await _room(client)
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
