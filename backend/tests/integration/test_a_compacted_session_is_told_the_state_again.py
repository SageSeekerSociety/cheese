"""压缩过上下文的会话，下一条输入重新带上整份现状。

开场快照（成员、频道、项目目标、记忆索引……）在对话历史里，只在开场说一次，之后只补
变了的段落。压缩把那段历史换成一段摘要：没变的段落从窗口里没了，而平台记着「告诉过」，
就再也不会说。一条写在团队记忆里的项目规矩，就这样在同一个座位上一次被照做、下一次
被漏掉。压缩失败什么都没丢，不用重说。
"""

import uuid

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.turn.store.session_inputs import forget_opening, remember_opening
from app.domain.agent_session.services import AgentSessionService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import project_service, registered

pytestmark = pytest.mark.anyio

#: 开场快照里一段跟踪着的现状（`TRACKED_SECTIONS`），这个项目里它一直没变。
_SNAPSHOT = "## 这个项目的产物清单"


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="compacts")
        self.stops(screen, "Done.", session_id="compacts")
        return True


async def _prompts_around_a_compaction(factory, tmp_path, result: str) -> list[str]:
    """两轮，会话自己压缩一次（``result`` 是骨架报的结果），再一轮；交回每一轮
    发出去的消息。项目从头到尾没变。"""
    screen = Screen()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        topic_id = topic.id

    seen: list[str] = []

    async def turn(content: str) -> None:
        async for _ in svc.converse(
            topic_id=topic_id, author="u", content=content, summon=True
        ):
            pass
        await settle_turn(svc, topic_id)
        seen.append(screen.last_prompt or "")

    await turn("开始吧")
    await turn("接着做")
    # 会话空闲时压缩（`cheese_session(action=compact)` 写下的 `/compact`，或者骨架自己），按
    # Claude Code 报压缩的那两条记录。
    screen.record(topic_id, type="system", subtype="status", status="compacting")
    extra = {"compact_error": "too long"} if result == "failed" else {}
    screen.record(
        topic_id,
        type="system",
        subtype="status",
        status=None,
        compact_result=result,
        **extra,
    )
    await settle_turn(svc, topic_id)
    await turn("再接着做")
    return seen


async def test_after_a_compaction_the_next_input_tells_the_whole_state(
    client, tmp_path
):
    first, second, third = client.portal.call(
        lambda: _prompts_around_a_compaction(
            client.test_request_factory, tmp_path, "success"
        )
    )

    assert _SNAPSHOT in first
    # 什么都没变，没压缩之前不重说。
    assert _SNAPSHOT not in second
    # 压缩之后，窗口里已经没有那份现状了：下一条输入整份带上。
    assert _SNAPSHOT in third, third


async def test_a_failed_compaction_lost_nothing_and_tells_nothing(client, tmp_path):
    first, second, third = client.portal.call(
        lambda: _prompts_around_a_compaction(
            client.test_request_factory, tmp_path, "failed"
        )
    )

    assert _SNAPSHOT in first
    assert _SNAPSHOT not in second
    assert _SNAPSHOT not in third


async def _told_after_a_compaction_mid_send(factory) -> dict | None:
    """A turn reads what its conversation was told and sends only the changes;
    the compaction ends before the turn records what it told."""
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
    seat = {"agent_handle": "cheese", "harness": "claude-code"}
    told = {"roster": "a", "memory": "b"}
    await remember_opening(factory, topic_id=topic.id, digests=told, was=None, **seat)

    async with factory() as session:
        read = await AgentSessionService(session).told(
            topic.id, seat["agent_handle"], harness=seat["harness"]
        )
    await forget_opening(factory, conversation_id=topic.id, **seat)
    await remember_opening(
        factory, topic_id=topic.id, digests={**told, "roster": "c"}, was=read, **seat
    )

    async with factory() as session:
        return await AgentSessionService(session).told(
            topic.id, seat["agent_handle"], harness=seat["harness"]
        )


async def test_a_compaction_that_ends_mid_send_is_not_written_over(client):
    told = client.portal.call(
        lambda: _told_after_a_compaction_mid_send(client.test_request_factory)
    )

    # The turn told only what changed; the compaction took the rest out of the
    # window. Nothing may say the rest is still there, so the next input tells
    # the whole state.
    assert told is None


async def _after_a_compaction_that_could_not_be_made(factory) -> tuple:
    """A conversation that pointed at a native session, then a compaction that
    could not be made. What the next turn would resume by, and what it was told."""
    from app.domain.agent.turn.store.session_inputs import abandon_opening
    from app.domain.agent_session.services import AgentSessionService

    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        topic_id = topic.id
    seat = {"agent_handle": "cheese", "harness": "claude-code"}
    async with factory() as session:
        service = AgentSessionService(session)
        await service.ensure(topic_id, seat["agent_handle"], harness=seat["harness"])
        await service.remember(
            conversation_id=topic_id,
            agent_handle=seat["agent_handle"],
            resume_token="native-1",
            harness=seat["harness"],
        )
        await session.commit()
    from app.domain.agent.turn.store.session_inputs import remember_opening

    await remember_opening(
        factory,
        topic_id=topic_id,
        digests={"roster": "a"},
        was=None,
        **seat,
    )
    await abandon_opening(
        factory,
        conversation_id=topic_id,
        seat=seat["agent_handle"],
        harness=seat["harness"],
    )
    async with factory() as session:
        service = AgentSessionService(session)
        token = await service.resume_token(
            topic_id, seat["agent_handle"], harness=seat["harness"]
        )
        told = await service.told(
            topic_id, seat["agent_handle"], harness=seat["harness"]
        )
        ran = await service.has_run(topic_id)
    return token, told, ran


async def test_a_compaction_that_could_not_be_made_lets_the_next_turn_start_fresh(
    client,
):
    """The state that ended a conversation for good: past its window, and the
    summary that would shrink it could not be made. Every later turn re-sends
    the same history and is refused the same way, so the platform stops
    pointing the seat at that native session — the next turn opens a new one,
    and, its `told` also gone, tells it the whole project state."""
    token, told, ran = client.portal.call(
        lambda: _after_a_compaction_that_could_not_be_made(client.test_request_factory)
    )

    assert token is None, (
        "the next turn must not resume the conversation that cannot fit"
    )
    assert told is None, "a new session has to be told the whole state"
    # ...and the room still reads as one that has run: giving up on a
    # conversation is not the same as never having had one, and the 现场 tab
    # (and the compute pin) hang on this answer.
    assert ran is True


async def _two_seats_one_given_up(factory) -> dict:
    """Two seats in one room, both with a native session and both told the
    state. Only one of them has the conversation that could not be compacted."""
    from app.domain.agent.turn.store.session_inputs import abandon_opening
    from app.domain.agent_session.services import AgentSessionService

    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        topic_id = topic.id
    seats = {
        "cheese": "native-cheese",
        "other": "native-other",
    }
    async with factory() as session:
        service = AgentSessionService(session)
        for handle, token in seats.items():
            await service.ensure(topic_id, handle, harness="claude-code")
            await service.remember(
                conversation_id=topic_id,
                agent_handle=handle,
                resume_token=token,
                harness="claude-code",
            )
        await session.commit()
    for handle in seats:
        await remember_opening(
            factory,
            topic_id=topic_id,
            agent_handle=handle,
            harness="claude-code",
            digests={"roster": "a"},
            was=None,
        )
    await abandon_opening(
        factory,
        conversation_id=topic_id,
        seat="cheese",
        harness="claude-code",
    )
    out: dict = {}
    async with factory() as session:
        service = AgentSessionService(session)
        for handle in seats:
            out[handle] = {
                "token": await service.resume_token(
                    topic_id, handle, harness="claude-code"
                ),
                "told": await service.told(topic_id, handle, harness="claude-code"),
            }
    return out


async def test_giving_up_on_one_conversation_leaves_every_other_seat_alone(client):
    """A room seats several teammates, each with its own conversation. Clearing
    the one that cannot be compacted must not touch another's session or what it
    was told — the row is keyed by the agent, and the update carries that key."""
    seats = client.portal.call(
        lambda: _two_seats_one_given_up(client.test_request_factory)
    )

    assert seats["cheese"]["token"] is None
    assert seats["cheese"]["told"] is None
    assert seats["other"]["token"] == "native-other"
    assert seats["other"]["told"] == {"roster": "a"}


async def _abandon_a_seat_that_has_no_row(client) -> None:
    from app.domain.agent.turn.store.session_inputs import abandon_opening

    factory = client.test_request_factory
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        topic_id = topic.id
    await abandon_opening(
        factory,
        conversation_id=topic_id,
        seat="nobody-here",
        harness="claude-code",
    )


async def test_giving_up_on_a_conversation_that_was_never_there_changes_nothing(
    client,
):
    """A seat with no row of its own, or a conversation with no session yet: the
    update matches nothing and nothing is raised. The failure path this runs on
    must never be the reason a turn has no line."""
    client.portal.call(lambda: _abandon_a_seat_that_has_no_row(client))


async def _a_resumed_start_does_not_revive_the_given_up_conversation(factory) -> tuple:
    """The conversation is given up on, then a start that was already on its
    way reports the SAME native id it resumed under. That is a resumed start,
    not a new conversation: the give-up has to stand."""
    from app.domain.agent.turn.store.session_inputs import abandon_opening
    from app.domain.agent_session.services import AgentSessionService

    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        topic_id = topic.id
    async with factory() as session:
        service = AgentSessionService(session)
        await service.ensure(topic_id, "cheese", harness="claude-code")
        await service.remember(
            conversation_id=topic_id,
            agent_handle="cheese",
            resume_token="native-1",
            harness="claude-code",
        )
        await session.commit()
    await abandon_opening(
        factory, conversation_id=topic_id, seat="cheese", harness="claude-code"
    )
    # The in-flight start reports the id it was launched with.
    async with factory() as session:
        await AgentSessionService(session).remember(
            conversation_id=topic_id,
            agent_handle="cheese",
            resume_token="native-1",
            harness="claude-code",
        )
        await session.commit()
        token = await AgentSessionService(session).resume_token(
            topic_id, "cheese", harness="claude-code"
        )
    return token


async def test_a_resumed_start_does_not_revive_the_given_up_conversation(client):
    """`save` is what a resumed start writes too, and it reports the id it
    resumed under. Clearing the marker on that would put the seat back on the
    conversation that cannot fit, and the next turn would be refused the same
    way — the standing give-up has to survive a start that brought no new
    conversation."""
    token = client.portal.call(
        lambda: _a_resumed_start_does_not_revive_the_given_up_conversation(
            client.test_request_factory
        )
    )

    assert token is None
