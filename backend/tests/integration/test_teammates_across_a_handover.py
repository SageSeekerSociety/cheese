"""Two teammates in one room, and the backend changes hands.

Teammates in a room run side by side, each in a conversation of its own. What
the backend taking over does with the turns it finds — which ones are still
being worked on, which messages still need a turn started — is decided for
each teammate, never for the room as a whole: one teammate working says nothing
about another.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic_membership.services import TopicMemberService
from app.main import app
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import chat_ws_url, post_message, post_project


class StillWorking(StubChannel):
    """Every teammate takes its prompt and starts a long command."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        del reply
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.uses(topic_id, "Bash", agent=agent, command="sleep 600")


def _service(client, channel: StubChannel) -> ChatService:
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/teammates-handover-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return service


def _room_with_a_teammate(client) -> str:
    """A room holding the project's own agent and a second one, "Second"."""
    project = post_project(client, {"name": "Handover"}, owner="alice")
    data = project.json()["data"]

    async def seat() -> None:
        async with client.test_factory() as session:
            second = await AgentInstanceService(session).create(
                project_id=uuid.UUID(data["id"]),
                handle="second",
                type_name=None,
                display_name="Second",
            )
            await TopicMemberService(session).ensure_agent_seat(
                uuid.UUID(data["root_topic_id"]), agent_instance_handle(second.id)
            )
            await session.commit()

    asyncio.run(seat())
    return data["root_topic_id"]


def _say(client, room: str, content: str) -> None:
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": content})
        while ws.receive_json()["type"] != "user_block":
            pass


def _prompts(channel: StubChannel, room: str) -> dict[str, list[str]]:
    """What each conversation in the room was handed, by who holds it."""
    return {
        session.actor: [
            str(message["message"]["content"])
            for message in session.written
            if message.get("type") == "user"
        ]
        for (topic, _), session in channel.sessions.items()
        if topic == uuid.UUID(room)
    }


def _heard(channel: StubChannel, room: str, text: str) -> str | None:
    """Who in the room was handed ``text``, if anyone was."""
    return next(
        (
            actor
            for actor, prompts in _prompts(channel, room).items()
            if any(text in prompt for prompt in prompts)
        ),
        None,
    )


def _turns(client, room: str) -> list[AgentTurn]:
    async def read() -> list[AgentTurn]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(AgentTurn).where(
                        AgentTurn.conversation_id == uuid.UUID(room)
                    )
                )
            )

    return asyncio.run(read())


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_a_teammate_whose_session_is_gone_has_its_turn_closed(client):
    """Both teammates' sessions were working on their own when the backend
    changed hands, and only the first one's session still answers afterwards.
    The first one's turn goes on; the second one's is over, and is not left
    open because somebody else in the room is still working."""
    room = _room_with_a_teammate(client)
    before = StubChannel()
    _service(client, before)
    _say(client, room, "@芝士 跑一下测试")
    _say(client, room, "@Second 编一下文档")
    _until(
        lambda: (
            _heard(before, room, "跑一下测试") is not None
            and _heard(before, room, "编一下文档") is not None
            and all(turn.stopped_at for turn in _turns(client, room))
        ),
        "the two ordinary turns never finished",
    )
    first = _heard(before, room, "跑一下测试")
    second = _heard(before, room, "编一下文档")
    assert first != second
    fed = {turn.id for turn in _turns(client, room)}

    # Each session wakes up and works without being asked (a worker of its
    # finished): a turn nobody fed, one per teammate.
    topic = uuid.UUID(room)
    before.uses(topic, "Bash", agent=first, command="make test")
    before.uses(topic, "Bash", agent=second, command="make docs")
    _until(
        lambda: len([t for t in _turns(client, room) if t.id not in fed]) == 2,
        "the sessions' own turns were never opened",
    )

    runner = get_work_runner()
    client.portal.call(runner.let_go)
    client.portal.call(before.runtime.stop_listening)

    # The machine kept the first teammate's runner; the second one's is gone —
    # and the fixture SAYS so by name (FB-56 legacy③): an exact terminal
    # declaration, not a list shape the recover could misread as one.
    after = StubChannel()
    after.root = before.root
    after.sessions = {
        key: session for key, session in before.sessions.items() if key[1] != second
    }
    for session in after.sessions.values():
        session.channel = after
    after.report_gone(topic, "second", before.sessions[(topic, second)].session_id)
    replaced = _service(client, after)
    assert client.portal.call(replaced.recover_sessions) == 1
    client.portal.call(runner.resume_orphans, replaced)

    still_open = {
        turn.author for turn in _turns(client, room) if turn.stopped_at is None
    }
    assert still_open == {first}, still_open


def test_waiting_messages_for_two_teammates_both_get_their_turn(client):
    """Each teammate was named in a message the previous backend never started
    a turn for. The next backend starts both — one room, two conversations."""
    room = _room_with_a_teammate(client)
    channel = StubChannel()
    service = _service(client, channel)
    runner = get_work_runner()
    runner.hold_turns()
    _say(client, room, "@芝士 修一下登录页")
    _say(client, room, "@Second 看一下注册页")

    client.portal.call(runner.let_go)
    runner.start_turns()
    assert client.portal.call(runner.resume_lost_messages, service) == 2

    _until(
        lambda: (
            _heard(channel, room, "修一下登录页") is not None
            and _heard(channel, room, "看一下注册页") is not None
        ),
        "a waiting message never got its turn",
    )
    assert _heard(channel, room, "修一下登录页") != _heard(
        channel, room, "看一下注册页"
    )


def test_a_waiting_message_is_not_held_back_by_another_teammates_turn(client):
    """The first teammate is mid-turn; a message naming the second one was never
    started. The first one's turn will not read it — it was not addressed to
    them — so the next backend starts the second one's turn beside it."""
    room = _room_with_a_teammate(client)
    channel = StillWorking()
    service = _service(client, channel)
    _say(client, room, "@芝士 跑一下测试")
    _until(
        lambda: any(t.delivered_at for t in _turns(client, room)),
        "the first turn never reached its session",
    )
    runner = get_work_runner()
    runner.hold_turns()
    _say(client, room, "@Second 看一下注册页")

    client.portal.call(runner.let_go)
    runner.start_turns()
    assert client.portal.call(runner.resume_lost_messages, service) == 1

    _until(
        lambda: _heard(channel, room, "看一下注册页") is not None,
        "the second teammate's message never got its turn",
    )
    assert _heard(channel, room, "看一下注册页") != _heard(channel, room, "跑一下测试")


class NeverReady(StubChannel):
    """A machine that never gets a session ready: every prompt is assembled,
    and none of them is ever delivered."""

    async def precheck(self, session, *, needs_place):
        await asyncio.Event().wait()


def test_two_teammates_prompts_that_never_arrived_are_both_sent_again(client):
    """The backend changed hands while both teammates' prompts were still on
    their way. The next backend sends each one again, to the teammate it was
    for — not just the newer one, and not both into one conversation."""
    room = _room_with_a_teammate(client)
    _service(client, NeverReady())
    _say(client, room, "@Second 编一下文档")
    _say(client, room, "@芝士 跑一下测试")
    _until(
        lambda: (
            len(_turns(client, room)) == 2
            and all(turn.agent_handle for turn in _turns(client, room))
        ),
        "the two turns were never assembled",
    )
    assert not any(turn.delivered_at for turn in _turns(client, room))

    runner = get_work_runner()
    client.portal.call(runner.let_go)
    after = StubChannel()
    replaced = _service(client, after)
    assert client.portal.call(runner.resume_orphans, replaced) == 2

    _until(
        lambda: (
            _heard(after, room, "跑一下测试") is not None
            and _heard(after, room, "编一下文档") is not None
        ),
        "a prompt that never arrived was not sent again",
    )
    assert _heard(after, room, "跑一下测试") != _heard(after, room, "编一下文档")


class _Deaf(StubChannel):
    """One session stays reachable in the machine's table but never answers
    its ping (FB-56 legacy③): nobody's success may become its death
    certificate, and only an explicit terminal declaration closes its rows."""

    def __init__(self, deaf_actor: str):
        super().__init__()
        self._deaf = deaf_actor

    async def call(self, handle, method, params):
        if method == "ping" and handle.agent_handle == self._deaf:
            raise DeviceCallError("listed but deaf")
        return await super().call(handle, method, params)


def _seat_teammate(
    client, project_data: dict, room: str, handle: str, name: str
) -> None:
    async def seat() -> None:
        async with client.test_factory() as session:
            mate = await AgentInstanceService(session).create(
                project_id=uuid.UUID(project_data["id"]),
                handle=handle,
                type_name=None,
                display_name=name,
            )
            await TopicMemberService(session).ensure_agent_seat(
                uuid.UUID(room), agent_instance_handle(mate.id)
            )
            await session.commit()

    asyncio.run(seat())


def _room_with_two_teammates(client) -> str:
    project = post_project(client, {"name": "Handover3"}, owner="alice")
    data = project.json()["data"]
    room = data["root_topic_id"]
    _seat_teammate(client, data, room, "second", "Second")
    _seat_teammate(client, data, room, "third", "Third")
    return room


def test_an_unanswered_conversation_is_unknown_not_dead(client):
    """recover 后：keeper 答了 ping，B 在机器上但 ping 失败，D 被夹具点名删除。
    死亡证据只覆盖 D——B 是 unknown，它送达过的行保持 open（FB-56 legacy③：
    A 成功不证明 B 终止；超时/不可达不是终态）。"""
    room = _room_with_two_teammates(client)
    before = StubChannel()
    _service(client, before)
    _say(client, room, "@芝士 跑一下测试")
    _say(client, room, "@Second 编一下文档")
    _say(client, room, "@Third 画一下海报")
    _until(
        lambda: (
            _heard(before, room, "跑一下测试") is not None
            and _heard(before, room, "编一下文档") is not None
            and _heard(before, room, "画一下海报") is not None
            and all(turn.stopped_at for turn in _turns(client, room))
        ),
        "the three ordinary turns never finished",
    )
    keeper = _heard(before, room, "跑一下测试")
    mate_b = _heard(before, room, "编一下文档")
    mate_d = _heard(before, room, "画一下海报")
    topic = uuid.UUID(room)
    fed = {turn.id for turn in _turns(client, room)}
    before.uses(topic, "Bash", agent=keeper, command="make test")
    before.uses(topic, "Bash", agent=mate_b, command="make docs")
    before.uses(topic, "Bash", agent=mate_d, command="make poster")
    _until(
        lambda: len([t for t in _turns(client, room) if t.id not in fed]) == 3,
        "the sessions' own turns were never opened",
    )

    runner = get_work_runner()
    client.portal.call(runner.let_go)
    client.portal.call(before.runtime.stop_listening)

    after = _Deaf(mate_b)
    after.root = before.root
    after.sessions = {
        key: session
        for key, session in before.sessions.items()
        if key[1] != mate_d  # D 已不在机器上
    }
    for session in after.sessions.values():
        session.channel = after
    # D 的终止由夹具按名声明（机器上确已删除）；B 只聋，什么也没声明。
    after.report_gone(topic, "third", before.sessions[(topic, mate_d)].session_id)
    replaced = _service(client, after)
    assert client.portal.call(replaced.recover_sessions) == 1, (
        "只有 keeper 答了 ping，B 聋"
    )
    client.portal.call(runner.resume_orphans, replaced)

    b_sid = before.sessions[(topic, mate_b)].session_id
    d_sid = before.sessions[(topic, mate_d)].session_id
    assert not replaced.row_is_dead(topic, "second", b_sid), (
        "ping 失败=unknown，不是 dead"
    )
    assert replaced.row_is_dead(topic, "third", d_sid), "点名删除=精确 dead 正对照"

    still_open = {
        turn.author for turn in _turns(client, room) if turn.stopped_at is None
    }
    assert still_open == {keeper, mate_b}, still_open


def test_a_recovered_conversations_old_death_record_is_revoked(client):
    """B 第一次 recover 被夹具点名删除、记 dead；第二次 recover 同 seat 同 SID
    真回来（撤销声明且 ping 答了）——旧死亡键必须失效，B 新开的自启
    delivered 行保持 open，不能借旧键收口（FB-56 legacy③）。新
    generation/attempt 的 resume_token 不同，按构造借不了旧键。"""
    room = _room_with_a_teammate(client)
    before = StubChannel()
    _service(client, before)
    _say(client, room, "@芝士 跑一下测试")
    _say(client, room, "@Second 编一下文档")
    _until(
        lambda: (
            _heard(before, room, "跑一下测试") is not None
            and _heard(before, room, "编一下文档") is not None
            and all(turn.stopped_at for turn in _turns(client, room))
        ),
        "the two ordinary turns never finished",
    )
    mate_b = _heard(before, room, "编一下文档")
    topic = uuid.UUID(room)
    b_sid = before.sessions[(topic, mate_b)].session_id
    fed = {turn.id for turn in _turns(client, room)}
    before.uses(topic, "Bash", agent=mate_b, command="make docs")
    _until(
        lambda: len([t for t in _turns(client, room) if t.id not in fed]) == 1,
        "the session's own turn was never opened",
    )
    (old_row,) = [t for t in _turns(client, room) if t.id not in fed]

    runner = get_work_runner()
    client.portal.call(runner.let_go)
    client.portal.call(before.runtime.stop_listening)

    # 第一次 recover：B 被点名删除 → 记 dead。
    after = StubChannel()
    after.root = before.root
    after.sessions = {
        key: session for key, session in before.sessions.items() if key[1] != mate_b
    }
    for session in after.sessions.values():
        session.channel = after
    after.report_gone(topic, "second", b_sid)
    replaced = _service(client, after)
    assert client.portal.call(replaced.recover_sessions) == 1
    assert replaced.row_is_dead(topic, "second", b_sid), "点名删除：记 dead"

    # 第二次 recover：B 回到机器且答了 ping → 旧键失效。
    after.sessions[(topic, mate_b)] = before.sessions[(topic, mate_b)]
    before.sessions[(topic, mate_b)].channel = after
    after.report_back(topic, "second", b_sid)
    assert client.portal.call(replaced.recover_sessions) == 2
    assert not replaced.row_is_dead(topic, "second", b_sid), "真恢复：旧死亡键失效"

    # 旧轮由它自己的 Stop 正常合上（正对照：精确 Stop 关闭不受撤销影响），
    # 然后 B 在同一 seat 同一 SID 上新开一轮自启——新行不能借旧键被收。
    after.stops(topic, "docs done", agent=mate_b)
    _until(
        lambda: all(
            t.stopped_at is not None for t in _turns(client, room) if t.id == old_row.id
        ),
        "the old turn never closed on its Stop",
    )
    after.uses(topic, "Bash", agent=mate_b, command="make docs-again")
    deadline = time.monotonic() + 15
    while True:
        new_rows = [t for t in _turns(client, room) if t.id not in fed | {old_row.id}]
        if new_rows:
            break
        assert time.monotonic() < deadline, "the new turn never opened"
        time.sleep(0.05)
    client.portal.call(runner.resume_orphans, replaced)
    open_ids = {turn.id for turn in _turns(client, room) if turn.stopped_at is None}
    assert new_rows[0].id in open_ids, "新自启行借旧死亡键被收口"
    assert new_rows[0].session_id == b_sid, "新行盖的是这条会话自己的 id"


def test_terminal_evidence_closes_rows_even_with_zero_recovered(client):
    """零成功恢复边界（FB-56 legacy③）：recover 没有任何一个 ping 答上
    （discover 无 handles），但两条会话都被夹具按名声明终止——精确 terminal
    证据照样被消费，两条 delivered 行都收口，不因为「没人活着回来」就搁浅。"""
    room = _room_with_a_teammate(client)
    before = StubChannel()
    _service(client, before)
    _say(client, room, "@芝士 跑一下测试")
    _say(client, room, "@Second 编一下文档")
    _until(
        lambda: (
            _heard(before, room, "跑一下测试") is not None
            and _heard(before, room, "编一下文档") is not None
            and all(turn.stopped_at for turn in _turns(client, room))
        ),
        "the two ordinary turns never finished",
    )
    keeper = _heard(before, room, "跑一下测试")
    mate_b = _heard(before, room, "编一下文档")
    topic = uuid.UUID(room)
    fed = {turn.id for turn in _turns(client, room)}
    before.uses(topic, "Bash", agent=keeper, command="make test")
    before.uses(topic, "Bash", agent=mate_b, command="make docs")
    _until(
        lambda: len([t for t in _turns(client, room) if t.id not in fed]) == 2,
        "the sessions' own turns were never opened",
    )

    runner = get_work_runner()
    client.portal.call(runner.let_go)
    client.portal.call(before.runtime.stop_listening)

    after = StubChannel()
    after.root = before.root
    after.sessions = {}  # 机器上什么都不剩
    after.report_gone(topic, "cheese", before.sessions[(topic, keeper)].session_id)
    after.report_gone(topic, "second", before.sessions[(topic, mate_b)].session_id)
    replaced = _service(client, after)
    assert client.portal.call(replaced.recover_sessions) == 0, "零成功恢复"
    client.portal.call(runner.resume_orphans, replaced)

    still_open = {
        turn.author for turn in _turns(client, room) if turn.stopped_at is None
    }
    assert still_open == set(), still_open
