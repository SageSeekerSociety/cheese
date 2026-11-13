"""In a channel's main line, the AI teammate's answer quotes the message it
answers when another person spoke between the question and the answer, so a
reader sees which line it answers. Where nobody else spoke it does not, and
the teammate's second message of the turn does not either: its own first
message is not somebody else talking, whichever handle it was written under (a
teammate is written under its seat in one place and its instance in another).
"""

import pytest

from app.domain.agent.turn.store.assistant import persist_assistant_message
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered

pytestmark = pytest.mark.anyio

SEAT = "cheese"


async def _load(session, topic_id):
    return await session.get(Topic, topic_id)


async def _quiet(*_args, **_kwargs):
    return None


async def _seat(_session, _topic_id):
    return SEAT


async def _answer(factory, room, question, *, author: str | None = None) -> dict:
    stored = await persist_assistant_message(
        factory,
        load_topic=_load,
        prepare_mentions=_quiet,
        notify_mentions=_quiet,
        resolve_author=_seat,
        author=author,
        project_id=room.project_id,
        topic_id=room.id,
        text="在资料库的「组会」文件夹里。",
        # A turn a message started is named after that message.
        turn_id=question.id,
        reply_to=None,
        roster=[],
        topic_refs=[],
    )
    assert stored.payload is not None
    return stored.payload


async def _room(factory):
    async with factory() as session:
        await registered(session, "alice")
        project = await project_service(session).create(
            name="P", owner_handle="alice", forge_kind="github_app"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="开发", created_by="alice"
        )
        await session.commit()
        return room


async def _say(factory, room, author: str, content: str):
    async with factory() as session:
        block = await BlockRepository(session).add(
            project_id=room.project_id,
            conversation_id=room.id,
            author=author,
            author_type=AuthorType.participant,
            content=content,
        )
        await session.commit()
        return block


async def test_an_answer_others_talked_past_quotes_the_question(db_factory):
    room = await _room(db_factory)
    question = await _say(db_factory, room, "alice", f"<@{SEAT}> 报名说明放在哪？")
    await _say(db_factory, room, "bob", "周四的组会改在 B201。")

    answer = await _answer(db_factory, room, question)

    assert answer["reply_to"] == str(question.id)
    assert answer["reply_preview"]


async def test_an_answer_nobody_talked_past_quotes_nothing(db_factory):
    room = await _room(db_factory)
    question = await _say(db_factory, room, "alice", f"<@{SEAT}> 报名说明放在哪？")
    await _say(db_factory, room, "alice", "就是上学期那份。")

    answer = await _answer(db_factory, room, question)

    assert answer["reply_to"] is None


async def test_the_second_message_of_the_turn_quotes_nothing(db_factory):
    room = await _room(db_factory)
    question = await _say(db_factory, room, "alice", f"<@{SEAT}> 报名说明放在哪？")
    await _say(db_factory, room, "bob", "周四的组会改在 B201。")
    # The first message of the turn, under the teammate's instance handle.
    first = await _answer(db_factory, room, question, author="cheese-57f62f24d959")

    second = await _answer(db_factory, room, question)

    assert first["reply_to"] == str(question.id)
    assert second["reply_to"] is None


async def test_nobody_talked_past_a_question_the_teammate_already_answered(db_factory):
    room = await _room(db_factory)
    question = await _say(db_factory, room, "alice", f"<@{SEAT}> 报名说明放在哪？")
    await _say(db_factory, room, "cheese-57f62f24d959", "我看一眼就告诉你。")

    answer = await _answer(db_factory, room, question)

    assert answer["reply_to"] is None


async def test_an_answer_to_no_persons_message_quotes_nothing(db_factory):
    room = await _room(db_factory)
    said = await _say(db_factory, room, "cheese-57f62f24d959", "欢迎来到这个项目。")
    await _say(db_factory, room, "alice", "首页要改哪些地方？")

    answer = await _answer(db_factory, room, said)

    assert answer["reply_to"] is None
