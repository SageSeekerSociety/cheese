"""私聊不占机器：一段对话不该在替自己扣着一台机器。

机器是**按话题**分配的，而私聊是一个话题——照原样走下去，每个人和芝士的那一间
都在替一段对话占着一台机器（配了 Cloud 的部署上就是一台云主机）。所以私聊改走
一条不落地的路：后端自己向模型问一次，回答走和会话那条路同一个消费口。

这一份钉的是那条岔路真的岔开了，以及岔开之后没有把该有的东西丢掉：房间照样收到
回答、账照样结、失败照样有人话。反过来的一半同样重要——**房间**必须还走机器，
一个把所有话题都送上轻路的改动会让整个平台失去工具。
"""

import uuid

import pytest

from app.domain.agent import plain_chat
from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn, stub_compute

pytestmark = pytest.mark.anyio


class CountingScreen(StubChannel):
    """一个会记账的会话：私聊那条路上它一次都不该被碰到。"""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.prompts.append(prompt)
        self.starts(screen, session_id="s1")
        self.stops(screen, "机器上的回答", session_id="s1")
        return True


def _service(factory, tmp_path, screen: StubChannel) -> ChatService:
    return ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )


def _with_gateway_env(svc: ChatService) -> None:
    """让这一轮拿到一份「后端够得着」的模型环境。

    真实部署里这份 env 来自项目的虚拟网关 key；测试里只要它长得像有凭据就够了，
    因为出口本身是被替掉的。
    """

    async def fake(*a, **kw):
        return {
            "model": "claude-x",
            "env": {
                "ANTHROPIC_BASE_URL": "http://gw:4000",
                "ANTHROPIC_AUTH_TOKEN": "sk-test",
            },
        }, "gateway"

    svc._model_kwargs = fake  # type: ignore[method-assign]


async def _private_topic(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).get_or_create_private(
            project_id=project.id, user_handle="u"
        )
        ids = (project.id, topic.id)
        await session.commit()
    return ids


async def _messages(factory, topic_id: uuid.UUID) -> list:
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    return [b for b in blocks if b.kind == BlockKind.message]


async def _everything_said(factory, topic_id: uuid.UUID) -> str:
    """房间里除了本人说的话之外，出现的所有文字。

    不按块的种类挑：一轮失败落成的是事件而不是消息，而这条用例问的是「人有没有
    被告知」——按种类挑，就会在真的说了的时候报没说。
    """
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    return " ".join(
        b.content or "" for b in blocks if b.author_type != AuthorType.human
    )


async def _everything_said(factory, topic_id: uuid.UUID) -> str:
    """房间里除了本人说的话之外，出现的所有文字。

    不挑块的种类：一轮失败落成的是事件而不是消息，而这条用例问的是「人有没有被
    告知」——按种类挑就会在真的说了的时候报没说。"""
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    return " ".join(
        b.content or "" for b in blocks if b.author_type != AuthorType.human
    )


async def test_a_private_chat_never_touches_a_machine(client, tmp_path, monkeypatch):
    factory = client.test_factory  # type: ignore[attr-defined]
    screen = CountingScreen()
    svc = _service(factory, tmp_path, screen)
    _with_gateway_env(svc)

    asked: list[dict] = []

    async def fake_ask(**kwargs):
        asked.append(kwargs)
        return plain_chat.PlainReply(
            text="在的，怎么了", input_tokens=7, output_tokens=3
        )

    monkeypatch.setattr(plain_chat, "ask", fake_ask)

    _, topic_id = await _private_topic(factory)
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="在吗", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)

    # 一条也没有送进会话 = 没有机器被用到。
    assert screen.prompts == []
    assert len(asked) == 1
    # 房间里照样有回答，而且是这条路产生的那一句。
    said = [
        b.content
        for b in await _messages(factory, topic_id)
        if b.author_type == AuthorType.ai
    ]
    assert said == ["在的，怎么了"]


async def test_the_room_still_runs_on_a_machine(client, tmp_path, monkeypatch):
    """反过来的一半：一个把所有话题都送上轻路的改动会让整个平台失去工具。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    screen = CountingScreen()
    svc = _service(factory, tmp_path, screen)

    async def never(**kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("房间不该走私聊那条路")

    monkeypatch.setattr(plain_chat, "ask", never)

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="干活", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)

    assert screen.prompts, "房间那一轮应该被送进会话"


async def test_no_backend_credential_is_said_out_loud(client, tmp_path, monkeypatch):
    """走订阅的部署上后端没有凭据。这不是故障，但必须有人话——沉默的私聊和坏掉的
    私聊在房间里长得一模一样。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    svc = _service(factory, tmp_path, CountingScreen())

    async def no_credential(*a, **kw):
        return {
            "model": "claude-x",
            "env": {"CLAUDE_CODE_OAUTH_TOKEN": "oauth"},
        }, "native"

    svc._model_kwargs = no_credential  # type: ignore[method-assign]

    async def never(**kwargs):  # pragma: no cover - 没凭据就不该发出去
        raise AssertionError("没有凭据时不该真的去调模型")

    monkeypatch.setattr(plain_chat, "ask", never)

    _, topic_id = await _private_topic(factory)
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="在吗", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)

    said = await _everything_said(factory, topic_id)
    assert "凭据" in said, said


async def test_history_goes_with_every_call(client, tmp_path, monkeypatch):
    """这条路是无状态的：没有会话替它记着上下文，所以上下文得每次自己带。带漏了的
    表现是芝士每一句都像第一次见到你。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    svc = _service(factory, tmp_path, CountingScreen())
    _with_gateway_env(svc)

    seen: list[list[dict]] = []

    async def fake_ask(**kwargs):
        seen.append(kwargs["messages"])
        return plain_chat.PlainReply(text="嗯", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(plain_chat, "ask", fake_ask)

    _, topic_id = await _private_topic(factory)
    for text in ("第一句", "第二句"):
        async for _ in svc.converse(
            topic_id=topic_id, author="u", content=text, summon=True
        ):
            pass
        await settle_turn(svc, topic_id)

    assert len(seen) == 2
    # 第二次必须看得见第一次的往来，否则它每一轮都在从零开始。
    flat = " ".join(m["content"] for m in seen[-1])
    assert "第一句" in flat and "第二句" in flat
    # 协议要求首尾都是 user，且角色交替。
    assert seen[-1][0]["role"] == "user"
    assert seen[-1][-1]["role"] == "user"
    roles = [m["role"] for m in seen[-1]]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))
