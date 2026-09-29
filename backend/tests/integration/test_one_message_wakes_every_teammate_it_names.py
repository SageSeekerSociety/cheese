"""一条消息 @ 了两位队友，两位都得到它 —— 各开各的轮，谁也不等谁。

一间房坐得下好几位队友（#1192 把「这个房间的 agent」换成了名册上的席位），可发
消息这条路上「点了谁的名」只算得出一个答案：`post_user_message` 扫到第一个 @ 就
停下，投递那一侧也只把这条消息送给那一位。于是 @ 了两位队友的消息，第二位既不
收到、也不开轮 —— 人当着他的面说话，他一个字都不知道。

修法是把「这条消息点了谁」从单个答案改成一组：`agent_recipient` 仍是第一个（老
读者只认它），`agent_recipients` 是全部；投递按这一组逐席位扇出，每一位在自己
座位上开自己那一轮。一条消息仍然只落一次库，房间里看到的还是一条消息。

扇出去还得收得回：一条点名了两位的消息对**每一位**都还是待读的，所以「谁读过
了」也不能是一位替两位 —— 库里那个戳是单值的，`turn_history` 又是按它取数的，
先收工的那位把单值盖上，另一位还没读的这条会从他的窗口里整条消失。单值那个戳
因此等点名的那几位都读过才落（`mark_consumed`），逐席位的那个印记照旧先记。

没点名和只点一位的行为一个字都不该变：一位自己的名字被写了两遍仍是一次到达
（`agent_handles` 上每个席位只列一次），没人被叫的房间照旧谁也不跑。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block, BlockKind
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, finish_turn, stub_compute
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio

# 两位队友，各自的名字和 handle。房间建出来的时候名册上已经有项目的默认芝士，
# 所以下面每一条断言里「谁跑了」都是一件真发生过的事，而不是「房间里只有一位」。
TEAMMATES = (("planner", "规划师"), ("reviewer", "审稿人"))


def _said(message: dict) -> str:
    content = message["message"]["content"]
    return content if isinstance(content, str) else content[0]["text"]


async def _until(cond, timeout: float = 5.0) -> None:
    """给异步投递一点时间，但不由这里报错 —— 让断言去说发生了什么。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond() and loop.time() < deadline:
        await asyncio.sleep(0.01)


class SideBySide(StubChannel):
    """两个座位都开口了才收工。

    每一位都在自己的会话里等着另一位：修之前第二个座位根本不会开口，这一等就
    等穿 —— 红在这里，说的正是「第二位队友什么都没收到」。
    """

    def __init__(self) -> None:
        super().__init__()
        self.seats: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        self.both_here = asyncio.Event()

    def arrive(
        self, topic_id: uuid.UUID, message: dict, *, agent: str | None = None
    ) -> None:
        prompt = _said(message)
        self.seats.append(str(agent))
        self.prompts.append((str(agent), prompt))
        if len(self.seats) >= 2:
            self.both_here.set()
        # 和默认那个会话做的事一样，只是先不回答：输入进了会话，回答等两位都到
        # 齐了再各答各的。
        self.record(
            topic_id,
            agent=agent,
            type="command_lifecycle",
            command_uuid=message["uuid"],
            state="queued",
        )

        async def answer() -> None:
            await self.both_here.wait()
            self.emit_turn(topic_id, prompt, f"{agent} 答", agent=agent)

        asyncio.get_running_loop().create_task(answer())


async def _a_room_with_two_teammates(factory) -> tuple[uuid.UUID, dict[str, str]]:
    """一间坐着两位队友的房子（项目的默认芝士也在名册上）。

    交回房号和两位的**席位** handle —— 会话是按席位开的（一间房几位队友，各有
    各的会话），而 `planner` 那样的名字是实例名，两者不是同一个字符串。
    """
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="u"
        )
        agents = AgentInstanceService(session)
        seats: dict[str, str] = {}
        for handle, display in TEAMMATES:
            made = await agents.create(
                project_id=project.id,
                handle=handle,
                type_name=None,
                display_name=display,
            )
            seats[handle] = agent_instance_handle(made.id)
            await TopicMemberService(session).ensure_agent_seat(topic.id, seats[handle])
        await session.commit()
        return topic.id, seats


def _service(factory, screen: StubChannel, tmp_path) -> ChatService:
    return ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


async def _the_user_message(factory, topic_id: uuid.UUID) -> Block:
    """房间里最后一条人说的话 —— 这条消息在库里长什么样。"""
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(Block)
                .where(Block.topic_id == topic_id, Block.kind == BlockKind.message)
                .order_by(Block.created_at)
            )
        )
    said = [
        row for row in rows if not row.meta or "agent_recipient" in (row.meta or {})
    ]
    assert said, "房间里没有人说过话"
    return said[-1]


async def test_one_message_wakes_every_teammate_it_names(business_db_factory, tmp_path):
    """一条 @ 了两位队友的消息，两位各自开一轮，而且两轮同时在跑。

    修之前：只有第一位收到投递，第二位连会话都没有 —— 那一位的这一轮永远不会
    来。两边都要到，还要同时到（一个人被问到，另一个人不该先答完再说）。
    """
    factory = business_db_factory  # type: ignore[attr-defined]
    screen = SideBySide()
    svc = _service(factory, screen, tmp_path)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=10.0)
    runner.subscribe_messages()
    topic_id, seats = await _a_room_with_two_teammates(factory)

    await broker.receive_message(
        svc, topic_id, author="u", content="@规划师 @审稿人 两位一起看看"
    )

    # 等到几位就等到几位（修之前永远只到一位），先放行、让到了的那一轮收工，
    # 再说谁到了 —— 断言挂在最后，红的时候留下的是「谁没来」而不是一个超时。
    await _until(lambda: len(screen.seats) >= 2)
    screen.both_here.set()
    await finish_turn(svc, topic_id)
    await runner.drain()

    assert sorted(screen.seats) == sorted(seats.values()), screen.seats
    # 一间房一份会话：两位各在自己的会话里，谁也没被塞进对方那个。
    assert set(screen.sessions) == {
        (topic_id, seats["planner"]),
        (topic_id, seats["reviewer"]),
    }, sorted(screen.sessions)
    for seat, prompt in screen.prompts:
        assert "两位一起看看" in prompt, (seat, prompt)

    # 老读者只认的那个字段还在，并且它就是这组里的第一位；两位都在这一组里。
    block = await _the_user_message(factory, topic_id)
    recipients = (block.meta or {})["agent_recipients"]
    assert sorted(r["handle"] for r in recipients) == ["planner", "reviewer"], (
        recipients
    )
    assert (block.meta or {})["agent_recipient"] == recipients[0], block.meta
    assert all(r["mentioned"] is True for r in recipients), recipients


async def test_naming_one_teammate_wakes_that_seat_only(business_db_factory, tmp_path):
    """只点一位：跑的只有那一位，和修之前一模一样。"""
    factory = business_db_factory  # type: ignore[attr-defined]
    screen = StubChannel()
    svc = _service(factory, screen, tmp_path)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=10.0)
    runner.subscribe_messages()
    topic_id, seats = await _a_room_with_two_teammates(factory)

    await broker.receive_message(svc, topic_id, author="u", content="@审稿人 你来看看")

    # 会话是异步开起来的：先等那一位的会话真的出现，再断言只有它一个。
    await _until(lambda: bool(screen.sessions))
    await finish_turn(svc, topic_id)
    assert set(screen.sessions) == {(topic_id, seats["reviewer"])}, sorted(
        screen.sessions
    )
    block = await _the_user_message(factory, topic_id)
    recipient = (block.meta or {})["agent_recipient"]
    assert recipient["handle"] == "reviewer", recipient
    assert recipient["mentioned"] is True, recipient
    # 点到一位时那条记录和从前一样：没有「一组」这回事。
    assert "agent_recipients" not in (block.meta or {}), block.meta
    await runner.drain()


async def test_naming_the_same_teammate_twice_is_one_arrival(
    business_db_factory, tmp_path
):
    """同一位被点两次，还是一位：一次到达，一轮。"""
    factory = business_db_factory  # type: ignore[attr-defined]
    screen = StubChannel()
    svc = _service(factory, screen, tmp_path)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=10.0)
    runner.subscribe_messages()
    topic_id, seats = await _a_room_with_two_teammates(factory)

    await broker.receive_message(
        svc, topic_id, author="u", content="@规划师 你也在 @规划师 你说呢"
    )

    await _until(lambda: bool(screen.sessions))
    await finish_turn(svc, topic_id)
    assert set(screen.sessions) == {(topic_id, seats["planner"])}, sorted(
        screen.sessions
    )
    block = await _the_user_message(factory, topic_id)
    recipient = (block.meta or {})["agent_recipient"]
    assert recipient["handle"] == "planner", recipient
    assert len((block.meta or {}).get("agent_recipients") or [recipient]) == 1, (
        block.meta
    )
    await runner.drain()


async def test_one_seat_reading_it_does_not_read_it_for_the_other(
    business_db_factory, tmp_path
):
    """一位读完不能替另一位读完 —— 这条消息得同时留在窗口里和库的取数条件里。

    「读过了」在库里是单值的 `meta.consumed_turn`，而 `turn_history` 正是按它
    取数的（`consumed_turn IS NULL` 才拿回来）。一位的轮次收了工就把它盖上，另
    一位还没读的这条会从那一位的 history 里整条消失 —— 逐席位的 `consumed_by`
    再准也够不到一条没被查出来的块。所以单值那个戳要等点名的那几位都读过才落。

    直接在这儿盖章，不跑会话：要钉的是那位先收工的轮次**留下了什么**，而不是
    两位的会话谁先到 —— 真跑起来两位是赛跑，同一件事两个结果。
    """
    from app.domain.agent.chat import _pending_input_blocks
    from app.domain.block.models import consumed_by, consumed_turn
    from app.domain.block.repositories import BlockRepository

    factory = business_db_factory  # type: ignore[attr-defined]
    topic_id, _seats = await _a_room_with_two_teammates(factory)
    svc = _service(factory, StubChannel(), tmp_path)

    async def look(block_id: uuid.UUID) -> tuple[Block, list[Block]]:
        async with factory() as session:
            block = await session.get(Block, block_id)
            history = await BlockRepository(session).turn_history(topic_id)
            return block, history

    async def stamp(block_id: uuid.UUID, by: str) -> None:
        """一轮干净收工盖的章，和 `_run` 收工那一刻盖的是同一种。"""
        async with factory() as session:
            await BlockRepository(session).mark_consumed(
                [block_id], uuid.uuid4(), by=by
            )
            await session.commit()

    # 一条点名了两位的消息：对两位都还是待读的。
    await svc.post_user_message(
        topic_id,
        author="u",
        content="@规划师 @审稿人 一起看看这个",
        turn_id=None,
        reply_to=None,
    )
    block = await _the_user_message(factory, topic_id)
    assert not consumed_by(block, "planner")
    assert not consumed_by(block, "reviewer")

    # 规划师先收工。他的章只算他读过了。
    await stamp(block.id, "planner")
    block, history = await look(block.id)
    assert consumed_by(block, "planner")
    assert not consumed_by(block, "reviewer"), block.meta
    # 这条还没读完，所以它得留在库里能取回的那一批里 —— 修之前恰恰是这里没了。
    assert block.id in [b.id for b in history], [b.content[:20] for b in history]
    assert consumed_turn(block) is None, block.meta
    # 于是审稿人下一轮组装 prompt 时它还在待读窗口里躺着。
    assert block.id in [
        b.id for b in _pending_input_blocks(history, for_handle="reviewer")
    ]
    # 而规划师自己那一轮不会把它再读一遍。
    assert block.id not in [
        b.id for b in _pending_input_blocks(history, for_handle="planner")
    ]

    # 审稿人也收工。两位都读过了，这条才算读完：单值落下去，两位都不再看得见。
    await stamp(block.id, "reviewer")
    block, history = await look(block.id)
    assert consumed_by(block, "planner")
    assert consumed_by(block, "reviewer")
    assert consumed_turn(block) is not None, block.meta
    assert block.id not in [b.id for b in history], [b.content[:20] for b in history]
    for handle in ("planner", "reviewer"):
        assert block.id not in [
            b.id for b in _pending_input_blocks(history, for_handle=handle)
        ]

    # 只点名一位的那条一个字都没变：一位收工就是整条读完，单值照旧。
    await svc.post_user_message(
        topic_id, author="u", content="@审稿人 只有你", turn_id=None, reply_to=None
    )
    one = await _the_user_message(factory, topic_id)
    await stamp(one.id, "reviewer")
    one, history = await look(one.id)
    assert consumed_turn(one) is not None, one.meta
    assert one.id not in [b.id for b in history], [b.content[:20] for b in history]


async def test_a_message_naming_nobody_wakes_nobody(business_db_factory, tmp_path):
    """没人被叫的房间照旧谁也不跑 —— 那句话由下一次召唤的窗口捎上。"""
    factory = business_db_factory  # type: ignore[attr-defined]
    screen = StubChannel()
    svc = _service(factory, screen, tmp_path)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=10.0)
    runner.subscribe_messages()
    topic_id, _seats = await _a_room_with_two_teammates(factory)

    await broker.receive_message(svc, topic_id, author="u", content="先记一句")
    await asyncio.sleep(0.1)

    assert screen.sessions == {}, sorted(screen.sessions)
    block = await _the_user_message(factory, topic_id)
    recipient = (block.meta or {})["agent_recipient"]
    assert recipient["mentioned"] is False, recipient
    await runner.drain()
