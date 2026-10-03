"""现场必须实时 (协作软件语义): a human post persists + broadcasts INSTANTLY,
never queued behind a running agent turn — the per-topic lock serializes only
the AI part of a turn. Pre-fix, the second converse() below deadlocks until the
slow agent finishes; the wait_for(2s) would blow up."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.compute_configs import (
    ComputeChoice,
    ProjectComputeConfigs,
    room_choice,
    standard_choice,
)
from app.domain.agent.harness import harness_for
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent_session.models import AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    agent_instance_handle,
    looks_like_agent_handle,
)
from app.domain.identity.services import IdentityService
from app.domain.project.services import ProjectService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from app.domain.topic_membership.repositories import TopicMembershipRepository
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, finish_turn, stub_compute
from tests.integration.conftest import registered
from tests.support.hang import HANG_S
from tests.support.quoted_context import prompt_quote, slide_quote


@pytest.mark.anyio
async def test_broker_live_quote_uses_saved_data_and_retries_only_once(
    business_db_factory, tmp_path
):
    from app.domain.agent.compute import ComputePool
    from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
    from app.domain.agent_instance.services import AgentInstanceService
    from app.domain.block.models import consumed_turn

    factory = business_db_factory
    provider = _SlowLiveScreen()
    svc = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider.runtime], provider.name),
    )
    async with factory() as session:
        await registered(session, "alice")
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="alice"
        )
        second = await AgentInstanceService(session).create(
            project_id=project.id,
            handle="second",
            type_name=None,
            display_name="Second",
        )
        members = TopicMemberService(session)
        current = (await members.agent_handles(topic.id))[0]
        other = agent_instance_handle(second.id)
        await members.ensure_agent_seat(topic.id, other)
        topic_id = topic.id
        await session.commit()

    async def first_turn():
        return [
            frame
            async for frame in svc.converse(
                topic_id=topic_id, author="alice", content="跑很久的命令", summon=True
            )
        ]

    run = asyncio.create_task(first_turn())
    await asyncio.wait_for(provider.started.wait(), HANG_S)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    runner.subscribe_messages()
    quote = slide_quote(f"  @Second <@{other}> <@{current}>\n")
    client_id = str(uuid.uuid4())
    try:
        landed = await broker.receive_message(
            svc,
            topic_id,
            author="alice",
            content=f"<@{current}> 解释这页",
            quoted_context=quote,
            client_id=client_id,
        )
        async with asyncio.timeout(HANG_S):
            while not provider.delivered:
                await asyncio.sleep(0.01)
        assert len(provider.delivered) == 1
        assert prompt_quote(provider.delivered[0]) == quote
        assert provider.runs == 1
        async with factory() as session:
            saved = await BlockRepository(session).get(landed)
            assert saved.meta["quoted_context"] == quote
            assert consumed_turn(saved) is None
        await svc.confirm_prompt_receipt(topic_id, provider.delivered[0])
        async with factory() as session:
            saved = await BlockRepository(session).get(landed)
            assert consumed_turn(saved) is not None
        again = await broker.receive_message(
            svc,
            topic_id,
            author="alice",
            content=f"<@{current}> 解释这页",
            quoted_context={**quote, "version": "version-b", "text": "改过的页面"},
            client_id=client_id,
        )
        assert again == landed
        assert len(provider.delivered) == 1
        async with factory() as session:
            saved = await BlockRepository(session).get(landed)
            assert saved.meta["quoted_context"] == quote
    finally:
        provider.release.set()
        await asyncio.wait_for(run, HANG_S)
        await runner.drain(timeout_s=60)
        await finish_turn(svc, topic_id)


def _said(message: dict) -> str:
    content = message["message"]["content"]
    return content if isinstance(content, str) else content[0]["text"]


class SlowScreen(StubChannel):
    """A session that works for minutes: it takes the prompt and answers only
    once released. Several seats answer side by side: being busy holds back
    only a SECOND write to the same seat's session, never another seat's."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.runs = 0
        self.delivered: list[str] = []
        self.prompts: dict[str, str] = {}
        self._answering: dict[str, asyncio.Task] = {}

    def arrive(
        self, topic_id: uuid.UUID, message: dict, *, agent: str | None = None
    ) -> None:
        prompt = _said(message)
        seat = agent or ""
        if seat in self._answering:
            # A write into a session that is already working: it reads it at
            # its next tool boundary, and echoes it only then.
            self.delivered.append(prompt)
            return
        self.runs += 1
        self.last_prompt = prompt
        self.prompts[seat] = prompt
        self.acknowledges(topic_id, prompt, agent=agent)
        self.started.set()
        task = asyncio.get_running_loop().create_task(self._answer(topic_id, agent))
        self._answering[seat] = task
        task.add_done_callback(lambda _t, s=seat: self._answering.pop(s, None))

    async def _answer(self, topic_id: uuid.UUID, agent: str | None) -> None:
        await self.release.wait()
        self.says(topic_id, "done", agent=agent)
        self.stops(topic_id, "done", agent=agent)


class InstantScreen(StubChannel):
    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id, session_id="s-affinity")
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, "done")
        self.stops(topic_id, "done", session_id="s-affinity")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "attachments", "expected_blocks"),
    [
        ("do this", [{"path": "room/a.png", "mime": "image/png"}], 2),
        ("", [{"path": "room/a.png", "mime": "image/png"}], 1),
    ],
)
async def test_retried_client_delivery_is_persisted_and_submitted_once(
    business_db_factory, tmp_path, content, attachments, expected_blocks
):
    from app.domain.agent.runtime import InProcessBroker

    factory = business_db_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    broker = InProcessBroker()
    submitted = []
    broker.subscribe_messages(lambda *args, **kwargs: submitted.append((args, kwargs)))
    async with broker.subscribe(str(topic_id)) as browser:
        first, second = await asyncio.gather(
            broker.receive_message(
                svc,
                topic_id,
                author="u",
                content=content,
                attachments=attachments,
                client_id="same-browser-delivery",
            ),
            broker.receive_message(
                svc,
                topic_id,
                author="u",
                content=content,
                attachments=attachments,
                client_id="same-browser-delivery",
            ),
        )
        echoes = [browser.get_nowait() for _ in range(expected_blocks * 2)]
        assert browser.empty()

    assert first == second
    assert len(submitted) == 1
    assert {frame["block"]["id"] for frame in echoes} == {
        frame["block"]["id"] for frame in echoes[:expected_blocks]
    }
    async with factory() as session:
        rows = [
            block
            for block in await BlockRepository(session).list_for_topic(topic_id)
            if not looks_like_agent_handle(block.author)
        ]
    assert len(rows) == expected_blocks
    anchor = next(block for block in rows if block.id == first)
    assert anchor.meta["client_id"] == "same-browser-delivery"


@pytest.mark.anyio
async def test_retry_adopts_a_pre_idempotency_delivery_without_resubmitting(
    business_db_factory, tmp_path
):
    from app.domain.agent.runtime import InProcessBroker

    factory = business_db_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()
    _payloads, original, original_ids, _ = await svc.post_user_message(
        topic_id,
        author="u",
        content="saved by the old backend",
        turn_id=None,
        reply_to=None,
        attachments=[{"path": "room/a.png", "mime": "image/png"}],
    )
    async with factory() as session:
        anchor = await BlockRepository(session).get(original)
        assert anchor is not None
        anchor.meta = {**(anchor.meta or {}), "client_id": "pre-upgrade-delivery"}
        await session.commit()

    broker = InProcessBroker()
    submitted = []
    broker.subscribe_messages(lambda *args, **kwargs: submitted.append((args, kwargs)))
    retried = await broker.receive_message(
        svc,
        topic_id,
        author="u",
        content="saved by the old backend",
        attachments=[{"path": "room/a.png", "mime": "image/png"}],
        client_id="pre-upgrade-delivery",
    )

    assert retried == original
    assert submitted == []
    async with factory() as session:
        rows = [
            block
            for block in await BlockRepository(session).list_for_topic(topic_id)
            if not looks_like_agent_handle(block.author)
        ]
    assert [block.id for block in rows] == original_ids


@pytest.mark.anyio
async def test_receiving_a_message_mints_no_second_agent(business_db_factory, tmp_path):
    """收下一条消息，收件人是项目建出来时就有的那个芝士，不多长一个队友。

    「读一条消息」不该建参与者。以前这条守的是反面——项目可以一个 agent 都没有，
    读消息也不许给它补一个；现在项目建出来就带着它的芝士，所以要守的是数目不变。
    """
    from app.domain.agent_instance.repositories import AgentInstanceRepository

    factory = business_db_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        project_id, topic_id = project.id, topic.id
        before = [
            agent.id
            for agent in await AgentInstanceRepository(session).list_for_project(
                project_id
            )
        ]
        await session.commit()
    assert before, "建项目就该播下芝士那一行"
    payloads, _, _, _ = await svc.post_user_message(
        topic_id, author="u", content="A note for later", turn_id=None, reply_to=None
    )
    assert payloads[0]["meta"]["agent_recipient"]["handle"] == "cheese"
    async with factory() as session:
        after = [
            agent.id
            for agent in await AgentInstanceRepository(session).list_for_project(
                project_id
            )
        ]
    assert after == before


class ProcessNotesScreen(StubChannel):
    """A turn that narrates as it works: two assistant messages, then the end."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, "Read workspace files.")
        self.says(topic_id, "The plan is ready.")
        self.stops(topic_id, "The plan is ready.", session_id="s-notes")


@pytest.mark.anyio
async def test_queued_message_retains_selected_teammate(business_db_factory, tmp_path):
    from app.domain.agent_instance.services import AgentInstanceService

    factory = business_db_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        agents = AgentInstanceService(session)
        first = await agents.create(
            project_id=project.id,
            handle="first",
            type_name=None,
            display_name="First",
        )
        second = await agents.create(
            project_id=project.id,
            handle="second",
            type_name=None,
            display_name="Second",
        )
        members = TopicMemberService(session)
        for made in (first, second):
            await members.ensure_agent_seat(topic.id, agent_instance_handle(made.id))
        topic_id = topic.id
        await session.commit()
    _, original, _, _ = await svc.post_user_message(
        topic_id, author="u", content="@First For first", turn_id=None, reply_to=None
    )
    await svc.post_user_message(
        topic_id, author="u", content="@Second For second", turn_id=None, reply_to=None
    )
    prepared = await svc._assemble_turn(
        topic_id=topic_id,
        content="@First For first",
        turn_id=original,
        user_block_id=original,
        provision_actor=None,
    )
    assert prepared.agent.handle == "first"
    assert prepared.pending_ids == [original]
    assert "For second" not in prepared.prompt_text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text,mentioned",
    [("@芝士 hello", True), ("芝士 hello", False), ("<@all> hello", False)],
)
async def test_backend_resolves_room_agent_mention(
    business_db_factory, tmp_path, text, mentioned
):
    factory = business_db_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()
    payloads, _, _, _ = await svc.post_user_message(
        topic_id, author="u", content=text, turn_id=None, reply_to=None
    )
    assert payloads[0]["meta"]["agent_recipient"]["mentioned"] is mentioned


@pytest.mark.anyio
async def test_backend_resolves_a_legacy_shared_seat_mention(
    business_db_factory, tmp_path
):
    """一间还挂着共用 ``cheese`` 席位的老房间，「@芝士」照样召得动坐在里面的那一位。

    共用席位是惰性迁走的（``migrate_shared_agent_seat``，等这间房的 agent 下次动手
    才迁），所以没动过的房间今天还挂着它——而它不是这个项目的实例：项目名册上叫
    「芝士」的是项目的默认实例，「@芝士」要是展开成它，就等于 @ 了一个没坐在这间房
    里的队友，这一轮起不来，通知反而发给了它。上面那条参数化用例覆盖的是新房间
    （席位就是实例的 handle），老席位这一支在这里。
    """
    factory = business_db_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        # 把这间房退回共用席位的样子：实例的席位撤掉，坐着的是 `cheese`。库里的存量
        # 行就长这样，迁移只在这间房的 agent 下次动手时才会碰它。
        seats = TopicMembershipRepository(session)
        seeded = await seats.get(
            topic_id=topic_id,
            member_handle=agent_instance_handle(project.default_agent_instance_id),
        )
        assert seeded is not None
        await seats.delete(seeded)
        await IdentityService(session).ensure_agent_user(handle=CHEESE_HANDLE)
        await TopicMemberService(session).ensure_agent_seat(topic_id, CHEESE_HANDLE)
        await session.commit()
    payloads, _, _, _ = await svc.post_user_message(
        topic_id, author="u", content="@芝士 hello", turn_id=None, reply_to=None
    )
    assert payloads[0]["content"] == f"<@{CHEESE_HANDLE}> hello"
    assert payloads[0]["meta"]["agent_recipient"]["mentioned"] is True


@pytest.mark.anyio
async def test_backend_mention_starts_when_browser_did_not_summon(
    business_db_factory, tmp_path
):
    from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker

    factory = business_db_factory
    screen = InstantScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    runner.subscribe_messages()
    await broker.receive_message(svc, topic_id, author="u", content="@芝士 check this")
    # The turn ends when it ends. A deadline here raced it and cancelled it
    # mid-turn when a loaded runner was slower than the deadline.
    await runner.drain(timeout_s=60)
    await finish_turn(svc, topic_id)
    assert "check this" in screen.last_prompt


@pytest.mark.anyio
async def test_other_teammate_message_runs_beside_the_live_turn(
    business_db_factory, tmp_path
):
    """点名另一位队友的消息不排队等当前这轮：那位队友的轮次当场并行起跑。

    一轮锁的只是自己那一席 —— 默认 agent 的轮次被捏住不放时，@Second 的
    消息起 Second 自己的一轮，两条会话同时在跑。
    """
    from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
    from app.domain.agent_instance.services import AgentInstanceService

    factory = business_db_factory
    screen = SlowScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        second = await AgentInstanceService(session).create(
            project_id=project.id,
            handle="second",
            type_name=None,
            display_name="Second",
        )
        second_seat = agent_instance_handle(second.id)
        await TopicMemberService(session).ensure_agent_seat(topic.id, second_seat)
        topic_id = topic.id
        await session.commit()
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="First task", summon=True
    ):
        pass
    await screen.started.wait()
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    runner.subscribe_messages()
    # 点名由服务端从正文算（I13）：`@Second` 落库时展开成它的席位，那一位队友的
    # handle 是 `second`，不以 `cheese` 开头 —— 寻址认席位才起得了这一轮。
    await broker.receive_message(
        svc, topic_id, author="u", content="@Second Second task"
    )
    # The default agent's turn is still held — Second's runs beside it: its
    # own session takes the prompt, and nothing is written mid-turn into the
    # default one's (merge_into_running_turn only delivers to the seat the
    # message named).
    async with asyncio.timeout(HANG_S):
        while screen.runs < 2:
            await asyncio.sleep(0.01)
    assert "Second task" in (screen.prompts.get(second_seat) or "")
    assert screen.delivered == []
    screen.release.set()
    # The turn ends when it ends. A deadline here raced it and cancelled it
    # mid-turn when a loaded runner was slower than the deadline.
    await runner.drain(timeout_s=60)
    await finish_turn(svc, topic_id)
    assert screen.runs == 2


class RacingSeat(ChatService):
    """Counts the turns inside `_converse_impl`, and parks the first one just
    before it takes its seat lock.

    `converse` asks "is a turn running?" and only then goes for the lock; the
    two are separated by `_turn_seat_handle`'s read of the roster. That gap is
    microseconds wide in production and unobservable from a test, so this
    subclass widens it: the first request stops inside the gap until
    `let_the_first_go`, which is exactly the interleaving the seat lock exists
    for — two requests that both saw the seat free.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seat_handle_calls = 0
        self.first_is_in_the_gap = asyncio.Event()
        self.let_the_first_go = asyncio.Event()
        self.in_flight = 0
        self.max_in_flight = 0
        self.turns = 0

    async def _turn_seat_handle(self, topic_id, **kwargs) -> str:
        self.seat_handle_calls += 1
        if self.seat_handle_calls == 1:
            self.first_is_in_the_gap.set()
            await self.let_the_first_go.wait()
        return await super()._turn_seat_handle(topic_id, **kwargs)

    async def _converse_impl(self, **kwargs):
        self.turns += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            async for frame in super()._converse_impl(**kwargs):
                yield frame
        finally:
            self.in_flight -= 1


class SeatLockRemoved(RacingSeat):
    """The same service with the lock taken away — a fresh lock per call is no
    lock at all. It exists to show the test below is not vacuous."""

    def _seat_lock_for(self, topic_id, agent_handle):
        return asyncio.Lock()


async def _a_topic(factory, tmp_path) -> uuid.UUID:
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()
    return topic_id


async def _converse_to_the_end(svc, topic_id: uuid.UUID, content: str) -> None:
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content=content, summon=True
    ):
        pass


async def _two_requests_that_both_saw_the_seat_free(
    svc: RacingSeat, topic_id: uuid.UUID
) -> tuple[asyncio.Task, asyncio.Task]:
    """Drive two summoning requests into the same gap, then let them out.

    Returns the two tasks with the second one's turn still running — the
    caller asserts, then releases the screen.
    """
    first = asyncio.create_task(_converse_to_the_end(svc, topic_id, "First task"))
    async with asyncio.timeout(HANG_S):
        while not svc.first_is_in_the_gap.is_set():
            if first.done():
                first.result()  # 它先炸了：把真正的错抛出来，别只报超时
            await asyncio.sleep(0.01)
    second = asyncio.create_task(_converse_to_the_end(svc, topic_id, "Second task"))
    # The second has to get through the same "is a turn running?" question and
    # answer "no" — otherwise it merges into a live turn and never races.
    async with asyncio.timeout(HANG_S):
        while svc.seat_handle_calls < 2:
            await asyncio.sleep(0.01)
    svc.let_the_first_go.set()
    return first, second


@pytest.mark.anyio
async def test_two_requests_that_both_saw_the_seat_free_never_overlap(
    business_db_factory, tmp_path
):
    """同一个席位同时只有一轮在跑 —— 哪怕两条请求都看见「席位空着」。

    两条消息各自拿到一轮是对的（它们都该被答）。错的是两轮**同时**在跑：
    一条回复流里插进另一条的产出。挡这件事的只有那把按 (topic, seat) 记的
    锁。同一间房里另一个席位的轮次照常并行，由
    `test_other_teammate_message_runs_beside_the_live_turn` 盖。
    """
    factory = business_db_factory
    screen = SlowScreen()
    svc = RacingSeat(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = await _a_topic(factory, tmp_path)
    first, second = await _two_requests_that_both_saw_the_seat_free(svc, topic_id)

    await asyncio.sleep(0.2)  # 让第一条从缝里走到锁上
    assert svc.turns == 1, "第二条那一轮还在跑，第一条这一轮就不该开"
    assert svc.in_flight == 1

    screen.release.set()
    await asyncio.wait_for(asyncio.gather(first, second), HANG_S)
    await finish_turn(svc, topic_id)
    assert svc.turns == 2, "两条消息都该拿到自己的一轮"
    assert svc.max_in_flight == 1, "同一席位的两轮不许同时在跑"


@pytest.mark.anyio
async def test_the_guard_bites_when_the_seat_lock_is_taken_away(
    business_db_factory, tmp_path
):
    """把锁拿走，上面那条断言就该红 —— 证明它不是空转。"""
    factory = business_db_factory
    screen = SlowScreen()
    svc = SeatLockRemoved(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = await _a_topic(factory, tmp_path)
    first, second = await _two_requests_that_both_saw_the_seat_free(svc, topic_id)

    await asyncio.sleep(0.2)
    assert svc.turns == 2, "没有锁，第一条不必等第二条"
    assert svc.max_in_flight == 2, "两轮同时在跑 —— 这正是上面那条守着的"

    screen.release.set()
    await asyncio.wait_for(asyncio.gather(first, second), HANG_S)
    await finish_turn(svc, topic_id)


class StillWorking(StubChannel):
    """Every seat takes its prompt and starts a long command, then says nothing."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.uses(topic_id, "Bash", agent=agent, command="sleep 600")


@pytest.mark.anyio
async def test_every_working_teammate_keeps_landing_after_a_restart(
    business_db_factory, tmp_path
):
    """两位队友在同一间房里各跑各的一轮，后端这时被换掉：新进程接回来以后，
    两位后来说的话都当场落进房间，不必等谁再被点名一次。"""
    from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
    from app.domain.agent_instance.services import AgentInstanceService

    factory = business_db_factory

    def service(channel: StubChannel) -> ChatService:
        return ChatService(
            session_factory=factory,
            compute=stub_compute(channel),
            base_system_prompt="You are Cheese.",
            workspace_root=str(tmp_path / "ws"),
        )

    before = StillWorking()
    svc = service(before)
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        second = await AgentInstanceService(session).create(
            project_id=project.id,
            handle="second",
            type_name=None,
            display_name="Second",
        )
        await TopicMemberService(session).ensure_agent_seat(
            topic.id, agent_instance_handle(second.id)
        )
        topic_id = topic.id
        await session.commit()
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="First task", summon=True
    ):
        pass
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    runner.subscribe_messages()
    await broker.receive_message(
        svc, topic_id, author="u", content="@Second Second task"
    )
    async with asyncio.timeout(HANG_S):
        while len(before.sessions) < 2 or len(before.runtime.work) < 2:
            await asyncio.sleep(0.01)

    # The old process stops reading; both sessions go on working on the machine.
    await before.runtime.stop_listening()
    after = StubChannel()
    after.root = before.root
    after.sessions = before.sessions
    for session_runner in after.sessions.values():
        session_runner.channel = after
    replaced = service(after)
    assert await replaced.recover_sessions() == 2

    seats = [session_runner.actor for session_runner in after.sessions.values()]
    for seat in seats:
        after.says(topic_id, f"still here: {seat}", agent=seat)

    # Nothing here drains a reader by hand: what lands is what the recovered
    # process reads on its own.
    expected = {f"still here: {seat}" for seat in seats}
    async with asyncio.timeout(HANG_S):
        while True:
            async with factory() as session:
                said = {
                    block.content
                    for block in await BlockRepository(session).list_for_topic(topic_id)
                }
            if expected <= said:
                break
            await asyncio.sleep(0.05)

    for seat in seats:
        after.stops(topic_id, "done", agent=seat)
    await finish_turn(replaced, topic_id)


@pytest.mark.anyio
async def test_execution_notes_are_retained_outside_public_replies(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(ProcessNotesScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        tid = topic.id
        await session.commit()
    async for _ in svc.converse(
        topic_id=tid, author="u", content="Write a plan", summon=True
    ):
        pass
    await finish_turn(svc, tid)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    replies = [
        b.content
        for b in rows
        if b.kind == BlockKind.message and looks_like_agent_handle(b.author)
    ]
    assert replies == []
    notes = [b for b in rows if (b.meta or {}).get("progress")]
    assert [b.content for b in notes] == ["Read workspace files.", "The plan is ready."]
    assert all(b.kind == BlockKind.event and b.meta["in_room"] is False for b in notes)


@pytest.mark.anyio
async def test_first_turn_materializes_inherited_compute_before_running(
    business_db_factory, tmp_path
):
    """Changing a later default must never move an existing topic session."""
    factory = business_db_factory  # type: ignore[attr-defined]
    screen = InstantScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )

    started_on = ComputeChoice(name="Eight cores", profile="cloud", cores=8)
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        project.settings = _default_compute(started_on)
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        project_id, topic_id = project.id, topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="start", summon=True
    ):
        pass
    await finish_turn(svc, topic_id)

    async with factory() as session:
        project = await ProjectService(session).get_or_404(project_id)
        project.settings = _default_compute(standard_choice("cloud"))
        topic = await TopicRepository(session).get(topic_id)
        assert topic is not None
        assert room_choice(topic, project.settings) == started_on
        # The room's own Cheese: its conversation is kept under the agent,
        # whichever seat the session authored under.
        resumes_by = await AgentSessionService(session).resume_token(
            topic_id, CHEESE_HANDLE, harness=harness_for(None)
        )
    assert resumes_by == "s-affinity"


def _default_compute(choice: ComputeChoice) -> dict:
    return {"compute_configs": ProjectComputeConfigs(default=choice).model_dump()}


class DeferredScreen(InstantScreen):
    """A session whose hands are leased later, per session — the central path."""

    deferred_work = True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "default",
    [
        ComputeChoice(name="Eight cores", profile="cloud", cores=8, memory_mb=16384),
        ComputeChoice(name="Lab workstation", profile="device", device_id="lab-box"),
    ],
    ids=["cloud-spec", "named-device"],
)
async def test_a_teammate_joining_later_starts_on_the_rooms_choice(
    business_db_factory, tmp_path, default
):
    """结论 60: the room's setting is the default for its new sessions, and it is
    what the room started with — the whole choice, not the pool it belongs to."""
    from app.domain.agent_instance.services import AgentInstanceService

    factory = business_db_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(DeferredScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        project.settings = _default_compute(default)
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        later = await AgentInstanceService(session).create(
            project_id=project.id,
            handle="later",
            type_name=None,
            display_name="Later",
        )
        await TopicMemberService(session).ensure_agent_seat(
            topic.id, agent_instance_handle(later.id)
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="start", summon=True
    ):
        pass
    await finish_turn(svc, topic_id)
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="@Later join in", summon=True
    ):
        pass
    await finish_turn(svc, topic_id)

    async with factory() as session:
        rows = list(
            await session.scalars(
                select(AgentSession).where(AgentSession.topic_id == topic_id)
            )
        )
    choices = {row.agent_handle: row.execution_request["choice"] for row in rows}
    assert set(choices) == {CHEESE_HANDLE, "later"}
    assert choices["later"] == choices[CHEESE_HANDLE] == default.model_dump()


@pytest.mark.anyio
async def test_post_lands_while_agent_turn_is_running(business_db_factory, tmp_path):
    # Use the shared Postgres-backed factory: the merged Base.metadata now carries
    # main's PG-only sequences (e.g. discussion_seq), which SQLite cannot create.
    factory = business_db_factory  # type: ignore[attr-defined]

    agent = SlowScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(agent),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async with factory() as session:
        await registered(session, "user-1")
        project = await ProjectService(session).create(name="P", owner_handle="user-1")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="user-1"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    # A summoned turn that parks mid-stream, holding the topic's turn lock.
    async def summoned() -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author="user-1", content="做点事", summon=True
            )
        ]

    turn = asyncio.create_task(summoned())
    await asyncio.wait_for(agent.started.wait(), 5)

    # While 芝士 is working, another human's plain post must land at once.
    async def post() -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author="user-2", content="我插一句", summon=False
            )
        ]

    frames = await asyncio.wait_for(post(), HANG_S)  # pre-fix: deadlocks here
    assert [f["type"] for f in frames] == ["user_block", "done"]
    assert frames[0]["block"]["content"] == "我插一句"
    assert frames[0]["block"]["author"] == "user-2"

    # The parked turn finishes normally afterwards.
    agent.release.set()
    await asyncio.wait_for(turn, 5)
    await finish_turn(svc, topic_id)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    assert [b.content for b in rows if looks_like_agent_handle(b.author)] == ["done"]


class FailingScreen(StubChannel):
    """A session that cannot be reached: the turn ends in an error result
    carrying the words the machine gave us, and nothing else."""

    def __init__(self, text: str, *, failure_code: str | None = None) -> None:
        super().__init__()
        self._text = text
        self._code = failure_code

    async def ensure(self, session, opening, live=None):
        del session, opening
        raise ScreenSetupError(self._text, failure_code=self._code)


@pytest.mark.anyio
async def test_a_failed_turn_says_what_failed_and_never_speaks_as_cheese(
    business_db_factory, tmp_path
):
    """本卡修的那个根因：`classify_platform_failure()` 没命中就**一个结构化字段
    都没有**，于是最常见的几条（AI 接口错误 / 余额用尽 / 座位限流）全都退化成
    「朴素系统行 + 整段原话」。

    现在：没命中也照样产出 `turn_failed` 的 meta，正文只留一行——而且那一行是
    **服务自己的原话**，不是「AI 服务返回错误」这种把原因埋起来的标签。原话原样
    躺在 `meta.detail` 里。

    而且它是一条系统事件，不是芝士说的话：把机器的报错顶着芝士的名字发出去，
    读的人会以为那是它的判断。
    """
    factory = business_db_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        # 座位限流不在任何一条分类规则里 —— 这正是要测的"没命中"。
        compute=stub_compute(
            FailingScreen("You've hit your session limit · resets 12:10pm (UTC)")
        ),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P2", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T2", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="做点事", summon=True
    ):
        pass
    await finish_turn(svc, topic_id)

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    # 机器的报错不是芝士说的话。
    assert not [b for b in rows if looks_like_agent_handle(b.author)]
    block = next(b for b in rows if b.kind == BlockKind.event)
    assert block.author == "system"
    # 一行，是服务的原话开头，而且整段原话不在正文里。
    assert "\n" not in block.content
    assert "session limit" in block.content
    assert "服务原话" not in block.content
    # 原话一字不差取得回来 —— 它没有第二个副本，丢了就真丢了。
    meta = block.meta
    assert meta["event_type"] == "turn_failed"
    assert meta["severity"] == "error"
    # 平台不自动重试，得有人再 @ 它。
    assert meta["who"] == "human"
    assert "session limit" in meta["detail"]
    assert meta["detail_label"] == "详细说明"


class StorageFullScreen(StubChannel):
    """The exact failure tmux skill staging produces when it hits ENOSPC. It
    must surface once as a platform event, not as an AI service blip."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def ensure(self, session, opening, live=None):
        del session, opening
        self.attempts += 1
        raise ScreenSetupError(
            "tmux 后端启动失败：[Errno 28] No space left on device: "
            "'/home/nictheboy/cheese-workspaces/private/SKILL.md'"
        )


@pytest.mark.anyio
async def test_storage_exhaustion_is_a_persistent_platform_event(
    business_db_factory, tmp_path
):
    factory = business_db_factory  # type: ignore[attr-defined]
    agent = StorageFullScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(agent),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="做点事", summon=True
    ):
        pass
    await finish_turn(svc, topic_id)

    assert agent.attempts == 1
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    block = next(b for b in rows if b.kind == BlockKind.event)
    meta = block.meta
    assert (meta["event_type"], meta["code"], meta["severity"]) == (
        "platform_error",
        "storage_exhausted",
        "error",
    )
    assert meta["retryable"] is True
    # 平台提示统一契约: 卡面留一句，解释性的那几句收进 detail 由前端折叠。
    assert meta["title"] and meta["detail"]
    assert block.content.count("。") == 1
    assert "/home/nictheboy" not in block.content
    assert "/home/nictheboy" not in block.meta["detail"]


class _SlowLiveScreen(SlowScreen):
    """One long-lived screen per topic, so a message that arrives while work is
    active goes into that same session."""

    name = "fake-live"
    embeds_images = False


@pytest.mark.anyio
async def test_summon_during_active_work_is_injected_without_a_second_done(
    business_db_factory, tmp_path
):
    """The platform used to be stricter than the tool it drives: an interactive
    Claude Code takes input while it works, but we serialized work on top, so a
    long command made every later @ wait. A second summon now goes into the live
    session and has no independent completion boundary."""
    from app.domain.agent.compute import ComputePool
    from app.domain.block.models import consumed_turn

    factory = business_db_factory  # type: ignore[attr-defined]
    provider = _SlowLiveScreen()
    svc = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider.runtime], provider.name),
    )

    async with factory() as session:
        await registered(session, "user-1")
        project = await ProjectService(session).create(name="P", owner_handle="user-1")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="user-1"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    async def summoned(author: str, content: str) -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author=author, content=content, summon=True
            )
        ]

    run = asyncio.create_task(summoned("user-1", "跑一个很久的命令"))
    await asyncio.wait_for(provider.started.wait(), 5)

    # Pre-fix this blocked until the active run finished.
    frames = await asyncio.wait_for(summoned("user-2", "等一下，先别跑"), HANG_S)
    assert all(frame["type"] != "done" for frame in frames)
    assert [p.split("\n\n", 1)[0] for p in provider.delivered] == [
        "[user-2]: 等一下，先别跑"
    ]
    # Injected, not queued: still exactly one active run.
    assert provider.runs == 1

    # The receipt is still the consumed boundary (#539 decision A) — but the
    # write-accept alone must NOT stamp: until the session echoes it back, the
    # message stays pending so a session death replays it.
    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    merged = [b for b in history if b.content == "等一下，先别跑"]
    assert len(merged) == 1
    assert consumed_turn(merged[0]) is None

    # The session consumes the injected text → its receipt stamps the block.
    await svc.confirm_prompt_receipt(topic_id, provider.delivered[0])
    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    merged = [b for b in history if b.content == "等一下，先别跑"]
    assert consumed_turn(merged[0]) is not None

    provider.release.set()
    await asyncio.wait_for(run, 5)
    await finish_turn(svc, topic_id)

    # Finishing the original run preserves that marker; later work will not
    # say the injected message all over again.
    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    merged = [b for b in history if b.content == "等一下，先别跑"]
    assert len(merged) == 1
    assert consumed_turn(merged[0]) is not None


@pytest.mark.anyio
async def test_failed_live_delivery_reports_error_then_queues_work(
    business_db_factory, tmp_path
):
    """A failed live handoff is visible before the message runs from the queue."""
    from app.domain.agent.compute import ComputePool

    factory = business_db_factory  # type: ignore[attr-defined]

    class _NoScreen(_SlowLiveScreen):
        """The live handoff fails at the transport: the second write does not
        land on the screen, so the message has to run from the queue instead."""

        name = "fake-noscreen"

        def __init__(self) -> None:
            super().__init__()
            # Set when the mid-turn write is ATTEMPTED. The test needs that
            # moment, and there is no other way to observe it: the attempt is
            # the whole subject, and it either happens while the first turn is
            # still working or it does not happen at all.
            self.tried = asyncio.Event()

        async def call(self, handle, method: str, params: dict) -> dict:
            if method == "steer":
                self.delivered.append(params["text"])
                self.tried.set()
                raise ScreenSetupError("屏幕没了")
            return await super().call(handle, method, params)

    provider = _NoScreen()
    svc = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider.runtime], provider.name),
    )

    async with factory() as session:
        await registered(session, "user-1")
        project = await ProjectService(session).create(name="P", owner_handle="user-1")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="user-1"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    async def summoned(author: str, content: str) -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author=author, content=content, summon=True
            )
        ]

    first = asyncio.create_task(summoned("user-1", "第一件事"))
    await asyncio.wait_for(provider.started.wait(), 5)

    second = asyncio.create_task(summoned("user-2", "第二件事"))
    # Wait for the write to be attempted, not for a slice of wall clock. A tenth
    # of a second used to stand in for "the second message has got as far as the
    # live handoff"; a loaded box does not honour that, and then the first turn
    # is released before the handoff happens — `deliver` finds no work in
    # flight, returns False without ever reaching the screen, and the test fails
    # on an empty `delivered` that says nothing about what it meant to check.
    await asyncio.wait_for(provider.tried.wait(), 5)
    assert not second.done()  # queued behind the lock, exactly as before

    provider.release.set()
    await asyncio.wait_for(first, 5)
    second_frames = await asyncio.wait_for(second, 5)
    assert provider.runs == 2
    assert [p.split("\n\n", 1)[0] for p in provider.delivered] == ["[user-2]: 第二件事"]

    fallback_frames = [
        frame
        for frame in second_frames
        if frame["type"] == "event_block"
        and frame["block"]["meta"].get("event_type") == "delivery_fallback"
    ]
    assert len(fallback_frames) == 1
    fallback = fallback_frames[0]["block"]
    assert fallback["meta"]["severity"] == "error"
    assert fallback["meta"]["who"] == "platform"
    assert all(frame["type"] != "error" for frame in second_frames)

    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    persisted = [
        block
        for block in history
        if (block.meta or {}).get("event_type") == "delivery_fallback"
    ]
    assert len(persisted) == 1
    await finish_turn(svc, topic_id)


@pytest.mark.anyio
async def test_midturn_delivery_holds_no_topic_lock(
    business_db_factory, tmp_path, monkeypatch
):
    """While the message is being handed to the machine, the topic row stays
    free for other writers (dev outage of 2026-09-18: a row lock held across a
    device call queued every writer of the row with a pool connection each)."""
    import asyncio

    from sqlalchemy import text

    factory = business_db_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()
    _payloads, _block_id, block_ids, _ = await svc.post_user_message(
        topic_id, author="u", content="改一下配色", turn_id=None, reply_to=None
    )
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def slow_deliver(tid, text, images=None, agent_handle=None, owes_reply=False):
        in_flight.set()
        await release.wait()
        return True

    monkeypatch.setattr(svc._compute, "deliver", slow_deliver)
    svc._active_turn_ids[topic_id] = {uuid.uuid4()}
    merge = asyncio.create_task(
        svc.merge_into_running_turn(topic_id, block_ids, "改一下配色", "u")
    )
    await asyncio.wait_for(in_flight.wait(), timeout=5)
    async with factory() as session:
        # NOWAIT fails at once if the delivery were holding the row.
        locked = await session.scalar(
            text("SELECT id FROM topics WHERE id = :id FOR UPDATE NOWAIT"),
            {"id": topic_id},
        )
        await session.rollback()
    assert locked == topic_id
    release.set()
    assert await asyncio.wait_for(merge, timeout=5) is True


@pytest.mark.anyio
async def test_midturn_message_stays_pending_until_its_receipt(
    business_db_factory, tmp_path, monkeypatch
):
    """#539 decision A: deliver() trusts the transport's write-accept, so the
    consumed stamp moves to the session echoing it back. Before that receipt
    the message stays pending (a session death replays it — 宁可重复不可丢失);
    only a receipt carrying the SAME injected text stamps it."""
    from app.domain.block.models import consumed_turn

    factory = business_db_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    _payloads, block_id, block_ids, _ = await svc.post_user_message(
        topic_id, author="u", content="改一下配色", turn_id=None, reply_to=None
    )

    delivered_texts: list[str] = []
    owed: list[bool] = []

    async def fake_deliver(tid, text, images=None, agent_handle=None, owes_reply=False):
        delivered_texts.append(text)
        owed.append(owes_reply)
        return True

    monkeypatch.setattr(svc._compute, "deliver", fake_deliver)
    turn_id = uuid.uuid4()
    svc._active_turn_ids[topic_id] = {turn_id}

    assert (
        await svc.merge_into_running_turn(topic_id, block_ids, "改一下配色", "u")
        is True
    )
    assert len(delivered_texts) == 1
    # A person's message: the session answers it before it does anything else.
    assert owed == [True]

    async def _consumed() -> bool:
        async with factory() as session:
            rows = await BlockRepository(session).list_for_topic(topic_id)
        row = next(b for b in rows if b.id == block_id)
        return consumed_turn(row) is not None

    # Write accepted but not yet consumed: must stay pending.
    assert await _consumed() is False
    # A receipt for some OTHER input must not stamp this message.
    await svc.confirm_prompt_receipt(topic_id, "别的输入")
    assert await _consumed() is False
    # The matching receipt stamps it.
    await svc.confirm_prompt_receipt(topic_id, delivered_texts[0])
    assert await _consumed() is True


class SlowToReplay(StubChannel):
    """A machine whose sessions in one room answer for their journal only once
    ``replayed`` is set: a backlog that takes a long time to hand over."""

    def __init__(self, slow: uuid.UUID) -> None:
        super().__init__()
        self.slow = slow
        self.replayed = asyncio.Event()

    async def call(self, handle, method: str, params: dict) -> dict:
        if method == "events" and handle.session.topic_id == self.slow:
            await self.replayed.wait()
        return await super().call(handle, method, params)


@pytest.mark.anyio
async def test_a_room_still_replaying_holds_only_its_own_turns(
    business_db_factory, tmp_path
):
    """后端换人时，一间房的会话积压很长、要回放很久：别的房间照常起轮次，这间房
    里点名芝士的那条等回放完再开跑，等得久了房间里会说一声。"""
    from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker

    factory = business_db_factory

    def service(channel: StubChannel) -> ChatService:
        return ChatService(
            session_factory=factory,
            compute=stub_compute(channel),
            base_system_prompt="You are Cheese.",
            workspace_root=str(tmp_path / "ws"),
        )

    before = StubChannel()
    svc = service(before)
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        rooms = [
            (
                await TopicService(session).create(
                    project_id=project.id, title=title, created_by="u"
                )
            ).id
            for title in ("Slow", "Quick")
        ]
        await session.commit()
    slow, quick = rooms
    for room in rooms:
        async for _ in svc.converse(
            topic_id=room, author="u", content="First task", summon=True
        ):
            pass
        await finish_turn(svc, room)

    await before.runtime.stop_listening()
    after = SlowToReplay(slow)
    after.root = before.root
    after.sessions = before.sessions
    for session_runner in after.sessions.values():
        session_runner.channel = after
    replaced = service(after)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    runner.REPLAY_NOTICE_S = 0.2
    runner.subscribe_messages()

    # Taking the sessions over does not wait for the slow room's backlog.
    async with asyncio.timeout(HANG_S):
        assert await replaced.recover_sessions() == 2

    await broker.receive_message(
        replaced, quick, author="u", content="@芝士 quick room task"
    )
    async with asyncio.timeout(HANG_S):
        while "quick room task" not in (after.last_prompt or ""):
            await asyncio.sleep(0.05)

    await broker.receive_message(
        replaced, slow, author="u", content="@芝士 slow room task"
    )

    async def notices() -> list[str]:
        async with factory() as session:
            return [
                block.content or ""
                for block in await BlockRepository(session).list_for_topic(slow)
            ]

    async with asyncio.timeout(HANG_S):
        while not any("断线期间" in text for text in await notices()):
            await asyncio.sleep(0.05)
    assert "slow room task" not in (after.last_prompt or "")

    after.replayed.set()
    async with asyncio.timeout(HANG_S):
        while "slow room task" not in (after.last_prompt or ""):
            await asyncio.sleep(0.05)
    await runner.drain(timeout_s=60)
    for room in rooms:
        await finish_turn(replaced, room)
